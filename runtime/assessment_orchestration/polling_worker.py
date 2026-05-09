from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterable

from azure.identity import DefaultAzureCredential

from runtime.log_config import configure_logging as _configure_logging
from runtime.provider_core import normalise_cloud_provider

from .intake import build_assessment_job_from_provider_event
from .interfaces import OrchestratorAdapter
from .mcp.confluence import ConfluenceMCPServer
from .runtime_wiring import (
    create_confluence_mcp_server_from_env,
    create_orchestrator_adapter_from_env,
)
from .state_store import CosmosPollingStateStore, PollingStateStore

_configure_logging("polling-worker", level=logging.WARN)

from ._framework_patterns import ALL_FRAMEWORK_ORDER as _ALL_FRAMEWORK_ORDER
from ._framework_patterns import DEFAULT_FRAMEWORK as _DEFAULT_FRAMEWORK_SCOPE
from ._framework_patterns import (
    is_explicit_all_framework_request as _is_explicit_all_framework_request,
)
from ._framework_patterns import requested_frameworks_from_text as _requested_frameworks_from_text


def _now_utc() -> datetime:
    """Run now utc."""
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """Run iso."""
    return dt.isoformat()


def _parse_iso(value: str) -> datetime:
    """Run parse iso."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _initial_since_from_lookback(lookback: str) -> str:
    """Convert ISO-like lookback (PT1H/PT30M/P1D) to an absolute since timestamp."""
    upper = (lookback or "").strip().upper()
    hours = minutes = days = 0
    m = re.search(r"(\d+)H", upper)
    if m:
        hours = int(m.group(1))
    m = re.search(r"(\d+)M(?!O)", upper)
    if m:
        minutes = int(m.group(1))
    m = re.search(r"P(\d+)D", upper)
    if m:
        days = int(m.group(1))
    delta = timedelta(hours=hours, minutes=minutes, days=days)
    if not delta:
        delta = timedelta(hours=1)
    return _iso(_now_utc() - delta)


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str, str]:
    """Run event sort key."""
    occurred_at = str(event.get("occurred_at") or "")
    title = str(event.get("title") or "")
    event_id = str(event.get("event_id") or "")
    return (occurred_at, title.lower(), event_id)


CONFLUENCE_COMMENT_MAX_CHARS = 32_767


def _render_finding_html(finding: dict[str, Any]) -> str:
    """Run render finding html."""
    requirement_id = escape(str(finding.get("requirement_id") or "unknown"))
    framework = escape(str(finding.get("framework") or "Unknown"))
    status = escape(str(finding.get("status") or "unknown").replace("_", " "))
    severity = escape(str(finding.get("severity") or "unknown"))
    rationale = escape(str(finding.get("rationale") or "No rationale provided."))
    parts = [
        "<li>",
        f"<strong>{requirement_id}</strong> ({framework}; {status}; severity {severity})<br/>{rationale}",
    ]
    gaps = [escape(str(item)) for item in (finding.get("gaps") or []) if str(item).strip()]
    recommendations = [
        escape(str(item)) for item in (finding.get("recommendations") or []) if str(item).strip()
    ]
    evidence_sources = [
        escape(str(item)) for item in (finding.get("evidence_sources") or []) if str(item).strip()
    ]
    if evidence_sources or gaps or recommendations:
        parts.append("<ul>")
        if evidence_sources:
            parts.append(
                f"<li><strong>Evidence:</strong> {escape(', '.join(evidence_sources))}</li>"
            )
        for gap in gaps[:3]:
            parts.append(f"<li><strong>Gap:</strong> {gap}</li>")
        for recommendation in recommendations[:3]:
            parts.append(f"<li><strong>Recommendation:</strong> {recommendation}</li>")
        parts.append("</ul>")
    parts.append("</li>")
    return "".join(parts)


def _render_assessment_comment_sections(assessment: dict[str, Any]) -> list[str]:
    """Return the assessment as a list of self-contained HTML sections.

    Each section is independently valid HTML.  Callers can pack them into
    one or more Confluence comments respecting the 32 767-character limit.
    """
    summary = str(assessment.get("executive_summary") or "Assessment completed.")
    findings = list(assessment.get("findings") or [])
    overall_risk = str(assessment.get("overall_risk_rating") or "unknown").replace("_", " ").title()
    recommended_actions = list(assessment.get("recommended_actions") or [])
    missing_evidence = list(assessment.get("missing_evidence") or [])
    citations = list(assessment.get("citations") or [])
    metadata = dict(assessment.get("metadata") or {})
    framework_scope = str(metadata.get("framework_scope") or "").strip()
    page_version = str(metadata.get("page_version") or "").strip()
    strategy = str(metadata.get("assessment_strategy") or "").strip()

    # --- Header section ---
    header_parts: list[str] = [
        "<p><strong>Automated compliance review</strong></p>",
    ]
    if framework_scope:
        header_parts.append(f"<p><strong>Framework scope:</strong> {escape(framework_scope)}</p>")
    if page_version:
        header_parts.append(f"<p><strong>Page version:</strong> {escape(page_version)}</p>")
    if strategy:
        header_parts.append(f"<p><strong>Assessment strategy:</strong> {escape(strategy)}</p>")
    header_parts += [
        f"<p><strong>Overall risk:</strong> {escape(overall_risk)}</p>",
        f"<p>{escape(summary)}</p>",
        f"<p><strong>Findings:</strong> {len(findings)}</p>",
    ]
    if findings:
        header_parts.append("<p><strong>Key findings</strong></p>")
    sections: list[str] = ["".join(header_parts)]

    # --- Per-finding sections ---
    for finding in findings:
        sections.append(f"<ul>{_render_finding_html(finding)}</ul>")

    # --- Footer section ---
    footer_parts: list[str] = []
    if missing_evidence:
        footer_parts.append("<p><strong>Missing evidence</strong></p><ul>")
        for item in missing_evidence[:10]:
            footer_parts.append(f"<li>{escape(str(item))}</li>")
        footer_parts.append("</ul>")
    if recommended_actions:
        footer_parts.append("<p><strong>Recommended actions</strong></p><ul>")
        for item in recommended_actions[:10]:
            footer_parts.append(f"<li>{escape(str(item))}</li>")
        footer_parts.append("</ul>")
    if citations:
        footer_parts.append("<p><strong>Citations</strong></p><ul>")
        for item in citations[:10]:
            footer_parts.append(f"<li>{escape(str(item))}</li>")
        footer_parts.append("</ul>")
    if footer_parts:
        sections.append("".join(footer_parts))

    return sections


def _pack_comment_bodies(
    sections: list[str],
    limit: int = CONFLUENCE_COMMENT_MAX_CHARS,
) -> list[str]:
    """Greedily pack sections into comment bodies, each within *limit* characters.

    If a single section exceeds *limit* it is hard-truncated with a suffix so
    the comment remains valid and within the Confluence storage limit.
    """
    comments: list[str] = []
    current: list[str] = []
    current_len = 0

    for section in sections:
        section_len = len(section)
        # Hard-truncate an oversized individual section
        if section_len > limit:
            truncated = section[: limit - 20] + "...[truncated]"
            section = truncated
            section_len = len(section)

        if current_len + section_len > limit:
            if current:
                comments.append("".join(current))
            current = [section]
            current_len = section_len
        else:
            current.append(section)
            current_len += section_len

    if current:
        comments.append("".join(current))

    return comments or [""]


def _render_assessment_comment(assessment: dict[str, Any]) -> str:
    """Render assessment as a single HTML comment body.

    Kept for backward compatibility. Use :func:`_pack_comment_bodies` with
    :func:`_render_assessment_comment_sections` for multi-comment splitting.
    """
    return "".join(_render_assessment_comment_sections(assessment))


def _post_assessment_comments(
    server: Any,
    target_id: str,
    *,
    assessment: dict[str, Any],
    identity_mode: str,
    idempotency_key: str,
    limit: int = CONFLUENCE_COMMENT_MAX_CHARS,
) -> None:
    """Render and post assessment, splitting across multiple comments when needed."""
    sections = _render_assessment_comment_sections(assessment)
    bodies = _pack_comment_bodies(sections, limit=limit)
    total = len(bodies)
    for i, body in enumerate(bodies):
        part_key = idempotency_key if total == 1 else f"{idempotency_key}-part{i + 1}of{total}"
        part_body = (
            body if total == 1 else (f"<p><em>Assessment comment {i + 1} of {total}</em></p>{body}")
        )
        delivery = server.post_comment(
            target_id,
            comment_body=part_body,
            identity_mode=identity_mode,
            idempotency_key=part_key,
        )
        if not delivery.success:
            raise RuntimeError(
                f"Failed posting Confluence comment part {i + 1}/{total} "
                f"for event {idempotency_key}: {delivery.failures}"
            )


def _render_no_change_comment(*, framework_scope: str, page_version: str) -> str:
    """Run render no change comment."""
    label = framework_scope or _DEFAULT_FRAMEWORK_SCOPE
    safe_label = escape(label)
    safe_version = escape(page_version)
    return (
        "<p><strong>Automated compliance review</strong></p>"
        f"<p><strong>Framework scope:</strong> {safe_label}</p>"
        f"<p><strong>Page version:</strong> {safe_version}</p>"
        "<p>No changes were detected on this Confluence page since the last assessment for this framework. "
        "A new page review was not triggered.</p>"
    )


def _render_framework_clarification_comment() -> str:
    """Run render framework clarification comment."""
    return (
        "<p><strong>Automated compliance review</strong></p>"
        "<p>Your request did not clearly specify a supported framework, so an assessment was not run.</p>"
        "<p><strong>Use one of these comment formats:</strong></p>"
        "<ul>"
        "<li>@compliance-agent Essential Eight</li>"
        "<li>@compliance-agent NIST CSF</li>"
        "<li>@compliance-agent ISM</li>"
        "<li>@compliance-agent PSPF</li>"
        "<li>@compliance-agent PCI DSS</li>"
        "<li>@compliance-agent CIS Controls</li>"
        "<li>@compliance-agent AESCSF</li>"
        "<li>@compliance-agent all frameworks</li>"
        "</ul>"
        "<p>Any comment that includes a supported framework name will be recognised. "
        "Supported frameworks: Essential Eight, ISM, AESCSF, NIST CSF, PSPF, PCI DSS, CIS Controls.</p>"
    )


def _content_hash(value: str) -> str:
    """Run content hash."""
    normalised = value.strip().encode("utf-8")
    return hashlib.sha256(normalised).hexdigest()


def _requested_frameworks_for_event(event: dict[str, Any]) -> tuple[str, ...]:
    """Run requested frameworks for event."""
    trigger_text = str(event.get("trigger_text") or "")
    requested_from_trigger = _requested_frameworks_from_text(trigger_text)
    if requested_from_trigger:
        return requested_from_trigger

    title = str(event.get("title") or "")
    return _requested_frameworks_from_text(title)


def _requested_frameworks_from_discussion_context(
    discussion_context: list[dict[str, Any]],
    *,
    mentioner_account_id: str = "",
    triggering_comment_id: str = "",
    server: ConfluenceMCPServer | None = None,
) -> tuple[str, ...]:
    """Run requested frameworks from discussion context."""
    mentioner = mentioner_account_id.strip()
    mention_markers = ("@compliance-agent", "@assessment-agent")
    triggering_id = triggering_comment_id.strip()

    if triggering_id:
        import logging

        logging.info(
            f"[polling_worker] Entering framework extraction for triggering_id={triggering_id}"
        )
        logging.info(f"[polling_worker] discussion_context: {repr(discussion_context)}")
        found_triggering_comment = False
        for item in discussion_context:
            comment_id = str(item.get("comment_id") or "").strip()
            if comment_id != triggering_id:
                continue
            found_triggering_comment = True
            text = str(item.get("text") or "").strip()
            if text:
                logging.info(
                    f"[polling_worker] Found text in discussion_context for id {triggering_id}: {repr(text)}"
                )
                frameworks = _requested_frameworks_from_text(text)
                if frameworks:
                    return frameworks
            # If text is missing, try to fetch it from Confluence API
            logging.info(
                f"[polling_worker] No text in discussion_context for id {triggering_id}, attempting API fetch..."
            )
            try:
                if server is not None and hasattr(server, "client") and server.client is not None:
                    comment = server.client.get_comment(triggering_id)
                    logging.info(
                        f"[polling_worker] Raw comment API response for id {triggering_id}: {repr(comment)}"
                    )
                    body = comment.get("body") or {}
                    storage = body.get("storage") or {}
                    comment_text = storage.get("value") or comment.get("bodyText") or ""
                    logging.info(
                        f"[polling_worker] Extracted comment_text for id {triggering_id}: {repr(comment_text)}"
                    )
                    comment_text = comment_text.strip()
                    if comment_text:
                        logging.info(
                            f"[polling_worker] Fetched comment text from API for id {triggering_id}: {repr(comment_text)}"
                        )
                        frameworks = _requested_frameworks_from_text(comment_text)
                        if frameworks:
                            return frameworks
                        # else fall through to aggregate candidate_texts
                    else:
                        logging.warning(
                            f"[polling_worker] Could not extract text from fetched comment for id {triggering_id}"
                        )
                else:
                    logging.warning(
                        "[polling_worker] No ConfluenceMCPServer instance or client available to fetch comment text."
                    )
            except Exception as e:
                logging.warning(
                    f"[polling_worker] Exception fetching comment text for id {triggering_id}: {e}"
                )
            # If the triggering comment cannot be resolved, fall through to the
            # rest of the discussion context. This preserves the existing
            # mention-driven behaviour for Confluence events that omit comment text.
        # If not found in discussion_context, attempt API fetch as fallback
        if not found_triggering_comment:
            logging.info(
                f"[polling_worker] Triggering comment id {triggering_id} not found in discussion_context. Attempting API fetch..."
            )
            try:
                if server is not None and hasattr(server, "client") and server.client is not None:
                    comment = server.client.get_comment(triggering_id)
                    logging.info(
                        f"[polling_worker] Raw comment API response for id {triggering_id}: {repr(comment)}"
                    )
                    body = comment.get("body") or {}
                    storage = body.get("storage") or {}
                    comment_text = storage.get("value") or comment.get("bodyText") or ""
                    logging.info(
                        f"[polling_worker] Extracted comment_text for id {triggering_id}: {repr(comment_text)}"
                    )
                    comment_text = comment_text.strip()
                    if comment_text:
                        logging.info(
                            f"[polling_worker] Fetched comment text from API for id {triggering_id}: {repr(comment_text)}"
                        )
                        frameworks = _requested_frameworks_from_text(comment_text)
                        if frameworks:
                            return frameworks
                        # else fall through to aggregate candidate_texts
                    else:
                        logging.warning(
                            f"[polling_worker] Could not extract text from fetched comment for id {triggering_id}"
                        )
                else:
                    logging.warning(
                        "[polling_worker] No ConfluenceMCPServer instance or client available to fetch comment text."
                    )
            except Exception as e:
                logging.warning(
                    f"[polling_worker] Exception fetching comment text for id {triggering_id}: {e}"
                )
            # If the triggering comment cannot be resolved, fall through to the
            # rest of the discussion context. This preserves the existing
            # mention-driven behaviour for Confluence events that omit comment text.
    candidate_texts: list[str] = []
    for item in discussion_context:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        author_id = str(item.get("author_id") or "").strip()
        if mentioner and author_id and author_id != mentioner:
            continue
        if not mentioner and not any(marker in text.lower() for marker in mention_markers):
            continue
        candidate_texts.append(text)

    combined = "\n".join(candidate_texts)
    if not combined:
        return ()
    return _requested_frameworks_from_text(combined)


@dataclass(frozen=True)
class PollerConfig:
    """PollerConfig."""

    source: str = "confluence"
    poll_interval_seconds: int = 75
    lease_ttl_seconds: int = 300
    initial_lookback: str = "PT1H"
    max_event_attempts: int = 3
    dry_run: bool = False
    space_keys: tuple[str, ...] = ()
    assessment_strategy: str = "single_pass"


@dataclass(frozen=True)
class PollCycleResult:
    """PollCycleResult."""

    acquired_lease: bool
    fetched_events: int
    processed_events: int
    terminal_failures: int
    watermark: str


def _build_recent_mentions_query(
    server: ConfluenceMCPServer,
    *,
    since_iso: str,
    window_end: datetime,
    space_keys: Iterable[str],
) -> list[dict[str, Any]]:
    """Run build recent mentions query."""
    scope = {"space_keys": list(space_keys)} if list(space_keys) else None
    result = server.get_recent_mentions(since=since_iso, scope_filter=scope)
    mentions = list(result.get("mentions") or [])
    bounded: list[dict[str, Any]] = []
    for mention in mentions:
        occurred_at = str(mention.get("occurred_at") or "")
        if not occurred_at:
            continue
        try:
            event_dt = _parse_iso(occurred_at)
        except Exception:
            # Keep the event rather than dropping it if Confluence returns
            # a non-ISO timestamp shape.
            bounded.append(mention)
            continue
        # Allow small forward skew to avoid dropping events whose fallback
        # timestamp is generated a moment after window_end is sampled.
        if event_dt <= (window_end + timedelta(minutes=5)):
            bounded.append(mention)
    bounded.sort(key=_event_sort_key)
    return bounded


def _process_assessment_event(
    *,
    adapter: OrchestratorAdapter,
    server: ConfluenceMCPServer,
    state_store: PollingStateStore,
    source: str,
    event: dict[str, Any],
    dry_run: bool,
    assessment_strategy: str = "single_pass",
) -> None:
    """Run process assessment event."""
    target_url = str(event.get("target_url") or "")
    target_id = str(event.get("target_id") or "")
    if not target_url or not target_id:
        raise ValueError(f"Event missing target reference fields: {event}")

    requested_frameworks = _requested_frameworks_for_event(event)
    include_discussion_context = not requested_frameworks

    artifact = server.get_content_by_id(
        target_id,
        identity_mode="app_only",
        include_discussion_context=include_discussion_context,
    )
    if not requested_frameworks:
        requested_frameworks = _requested_frameworks_from_discussion_context(
            list(getattr(artifact, "discussion_context", []) or []),
            mentioner_account_id=str(event.get("mentioner_account_id") or ""),
            triggering_comment_id=str(event.get("content_id") or ""),
            server=server,
        )

    current_page_version = str(artifact.metadata.get("version") or "")
    current_content_hash = _content_hash(artifact.content)
    page_title = str(getattr(artifact, "title", "") or event.get("title") or target_id)
    page_target_url = str(getattr(artifact, "canonical_url", "") or target_url)
    page_space_key = str(artifact.metadata.get("space_key") or event.get("space_key") or "")

    if not requested_frameworks:
        if dry_run:
            return
        state_store.upsert_page_assessment(
            source,
            target_id=target_id,
            framework_scope="Clarification Required",
            title=page_title,
            target_url=page_target_url,
            space_key=page_space_key,
            status="clarification_required",
            overall_risk="unknown",
            findings_count=0,
            assessed_at=_iso(_now_utc()),
            page_version=current_page_version,
        )
        event_key = str(event.get("event_id") or target_id)
        delivery = server.post_comment(
            target_id,
            comment_body=_render_framework_clarification_comment(),
            identity_mode="app_only",
            idempotency_key=f"{event_key}-clarify-framework",
        )
        if not delivery.success:
            raise RuntimeError(
                f"Failed posting Confluence clarification comment for event {event_key}: {delivery.failures}"
            )
        return

    framework_scopes = requested_frameworks

    for framework_scope in framework_scopes:
        framework_snapshot_scope = framework_scope or _DEFAULT_FRAMEWORK_SCOPE
        last_snapshot = state_store.get_assessment_snapshot(
            source,
            target_id=target_id,
            framework_scope=framework_snapshot_scope,
        )

        if (
            last_snapshot is not None
            and last_snapshot.page_version == current_page_version
            and last_snapshot.content_hash == current_content_hash
        ):
            if dry_run:
                continue

            state_store.upsert_page_assessment(
                source,
                target_id=target_id,
                framework_scope=framework_snapshot_scope,
                title=page_title,
                target_url=page_target_url,
                space_key=page_space_key,
                status="no_change",
                overall_risk="unknown",
                findings_count=0,
                assessed_at=_iso(_now_utc()),
                page_version=current_page_version,
            )

            event_key = str(event.get("event_id") or target_id)
            scope_key = re.sub(
                r"[^a-zA-Z0-9_\-]", "", framework_snapshot_scope.lower().replace(" ", "-")
            )
            idempotency_key = f"{event_key}-{scope_key}-nochange"
            delivery = server.post_comment(
                target_id,
                comment_body=_render_no_change_comment(
                    framework_scope=framework_snapshot_scope,
                    page_version=current_page_version,
                ),
                identity_mode="app_only",
                idempotency_key=idempotency_key,
            )
            if not delivery.success:
                raise RuntimeError(
                    f"Failed posting Confluence no-change comment for event {idempotency_key}: {delivery.failures}"
                )
            continue

        metadata: dict[str, Any] = {
            "trigger_text": str(event.get("trigger_text") or ""),
            "requested_frameworks": list(requested_frameworks),
            "review_scope_mode": (
                "all" if requested_frameworks == _ALL_FRAMEWORK_ORDER else "selected"
            ),
        }
        if framework_scope:
            metadata["requested_framework"] = framework_scope

        job = build_assessment_job_from_provider_event(
            {
                "event_id": event.get("event_id") or "",
                "target_id": target_id,
                "target_url": target_url,
                "trigger_type": event.get("trigger_type") or "mention",
                "requester_id": event.get("mentioner_account_id") or "",
                "metadata": metadata,
            },
            provider_hint="confluence",
            request_identity_mode="app_only",
            delivery_policy="inline_else_email",
        )
        if assessment_strategy == "per_control":
            assessment = adapter.run_per_control_assessment(job)
        else:
            assessment = adapter.run_assessment(job)
        assessment_metadata = dict(assessment.get("metadata") or {})
        if current_page_version:
            assessment_metadata["page_version"] = current_page_version
        if (
            framework_snapshot_scope
            and not str(assessment_metadata.get("framework_scope") or "").strip()
        ):
            assessment_metadata["framework_scope"] = framework_snapshot_scope
        assessment["metadata"] = assessment_metadata
        if dry_run:
            continue

        state_store.upsert_assessment_snapshot(
            source,
            target_id=target_id,
            framework_scope=framework_snapshot_scope,
            page_version=current_page_version,
            content_hash=current_content_hash,
        )
        state_store.upsert_page_assessment(
            source,
            target_id=target_id,
            framework_scope=framework_snapshot_scope,
            title=page_title,
            target_url=page_target_url,
            space_key=page_space_key,
            status="assessed",
            overall_risk=str(assessment.get("overall_risk_rating") or "unknown"),
            findings_count=len(list(assessment.get("findings") or [])),
            assessed_at=_iso(_now_utc()),
            page_version=current_page_version,
        )

        event_key = str(event.get("event_id") or job.correlation_id)
        scope_key = re.sub(r"[^a-zA-Z0-9_\-]", "", framework_scope.lower().replace(" ", "-"))
        idempotency_key = event_key if not scope_key else f"{event_key}-{scope_key}"
        _post_assessment_comments(
            server,
            target_id,
            assessment=assessment,
            identity_mode="app_only",
            idempotency_key=idempotency_key,
        )


def run_poll_cycle(
    *,
    config: PollerConfig,
    state_store: PollingStateStore,
    server: ConfluenceMCPServer,
    adapter: OrchestratorAdapter,
    process_event: Callable[[dict[str, Any]], None] | None = None,
) -> PollCycleResult:
    """Run run poll cycle."""
    run_id = str(uuid.uuid4())
    cycle_started = _now_utc()
    since_iso = ""
    if not state_store.try_acquire_lease(
        config.source,
        owner_run_id=run_id,
        ttl_seconds=config.lease_ttl_seconds,
    ):
        state = state_store.load_state(config.source)
        return PollCycleResult(
            acquired_lease=False,
            fetched_events=0,
            processed_events=0,
            terminal_failures=0,
            watermark=state.watermark,
        )

    processed_count = 0
    terminal_failures = 0
    fetched_events = 0
    watermark = ""
    cycle_error = ""
    try:
        state = state_store.load_state(config.source)
        now = _now_utc()
        window_end = now
        since_iso = state.watermark or _initial_since_from_lookback(config.initial_lookback)

        mentions = _build_recent_mentions_query(
            server,
            since_iso=since_iso,
            window_end=window_end,
            space_keys=config.space_keys,
        )
        fetched_events = len(mentions)
        watermark = state.watermark

        handler = process_event or (
            lambda event: _process_assessment_event(
                adapter=adapter,
                server=server,
                state_store=state_store,
                source=config.source,
                event=event,
                dry_run=config.dry_run,
                assessment_strategy=config.assessment_strategy,
            )
        )

        for event in mentions:
            event_id = str(event.get("event_id") or "")
            occurred_at = str(event.get("occurred_at") or "")
            if not event_id or not occurred_at:
                continue

            if state_store.is_event_processed(config.source, event_id):
                state = state_store.commit_state(
                    config.source,
                    watermark=occurred_at,
                    last_processed_event_id=event_id,
                    poll_count_increment=1,
                )
                watermark = state.watermark
                continue

            try:
                handler(event)
                state_store.mark_processed_event(config.source, event_id=event_id, run_id=run_id)
                state = state_store.commit_state(
                    config.source,
                    watermark=occurred_at,
                    last_processed_event_id=event_id,
                    poll_count_increment=1,
                )
                watermark = state.watermark
                processed_count += 1
            except Exception as exc:
                attempts = state_store.increment_failure_count(
                    config.source,
                    event_id=event_id,
                    error_message=str(exc),
                    run_id=run_id,
                )
                if attempts >= config.max_event_attempts:
                    state_store.mark_terminal_failure(
                        config.source,
                        event_id=event_id,
                        error_message=str(exc),
                        run_id=run_id,
                    )
                    state = state_store.commit_state(
                        config.source,
                        watermark=occurred_at,
                        last_processed_event_id=event_id,
                        last_error={"event_id": event_id, "error": str(exc), "terminal": True},
                        poll_count_increment=1,
                    )
                    watermark = state.watermark
                    terminal_failures += 1
                    continue

                # Retryable failure: stop and retry from this event in the next cycle.
                break

        if not mentions:
            watermark = state.watermark

        return PollCycleResult(
            acquired_lease=True,
            fetched_events=fetched_events,
            processed_events=processed_count,
            terminal_failures=terminal_failures,
            watermark=watermark,
        )
    except Exception as exc:
        cycle_error = str(exc)
        raise
    finally:
        state_store.upsert_poll_run_summary(
            config.source,
            polled_at=_iso(cycle_started),
            since_iso=since_iso,
            watermark=watermark,
            mentions_found=fetched_events,
            jobs_queued=processed_count,
            terminal_failures=terminal_failures,
            error_message=cycle_error,
            space_keys=config.space_keys,
        )
        state_store.release_lease(config.source, owner_run_id=run_id)


def run_forever(
    config: PollerConfig,
    *,
    state_store: PollingStateStore,
    server: ConfluenceMCPServer,
    adapter: OrchestratorAdapter,
) -> None:
    """Run run forever."""
    while True:
        run_poll_cycle(config=config, state_store=state_store, server=server, adapter=adapter)
        time.sleep(max(1, config.poll_interval_seconds))


def create_cosmos_state_store_from_env(
    env: dict[str, str] | None = None,
    *,
    aws_session: Any = None,
) -> PollingStateStore:
    """Create a poller state store from environment settings.

    Modes:
    - local (including dev alias): SQLite-backed state using LOCAL_STATE_DB_PATH when set,
      otherwise defaults to runtime/out/local_state.db.
    - aws: DynamoDB-backed state using DYNAMODB_TABLE.
    - azure/default: Cosmos-backed state (requires endpoint + database).
    """
    values = dict(os.environ) if env is None else dict(env)
    provider = normalise_cloud_provider(values.get("CLOUD_PROVIDER"))

    if provider == "local":
        from .sqlite_state_store import SqlitePollingStateStore

        default_local_path = str(
            (Path(__file__).resolve().parent.parent.parent / "runtime" / "out" / "local_state.db")
        )
        db_path = str(values.get("LOCAL_STATE_DB_PATH") or "").strip() or default_local_path
        try:
            return SqlitePollingStateStore(db_path)
        except Exception as exc:
            if db_path != default_local_path:
                logging.warning(
                    "Local poller state path %s unavailable (%s); falling back to %s",
                    db_path,
                    exc,
                    default_local_path,
                )
                return SqlitePollingStateStore(default_local_path)
            raise

    if provider == "aws":
        from .dynamo_state_store import DynamoDBPollingStateStore

        table_name = str(values.get("DYNAMODB_TABLE") or "").strip()
        region_name = str(
            values.get("AWS_REGION") or values.get("AWS_DEFAULT_REGION") or ""
        ).strip()
        return DynamoDBPollingStateStore(
            table_name=table_name or None,
            session=aws_session,
            region_name=region_name or None,
        )

    endpoint = str(values.get("AZURE_COSMOS_ENDPOINT") or "").strip()
    database_name = str(values.get("AZURE_COSMOS_DATABASE_NAME") or "").strip()
    container_name = str(
        values.get("AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME") or "orchestration-state"
    ).strip()
    if not endpoint or not database_name:
        raise ValueError("AZURE_COSMOS_ENDPOINT and AZURE_COSMOS_DATABASE_NAME are required")

    from azure.cosmos import CosmosClient

    credential = DefaultAzureCredential()
    client = CosmosClient(url=endpoint, credential=credential)
    db = client.get_database_client(database_name)
    container = db.get_container_client(container_name)
    return CosmosPollingStateStore(container)


def load_poller_config_from_env(env: dict[str, str] | None = None) -> PollerConfig:
    """Run load poller config from env."""
    values = dict(os.environ) if env is None else dict(env)
    poll_interval_seconds = int(values.get("CONFLUENCE_POLL_INTERVAL_SECONDS") or "75")
    lease_ttl_seconds = int(values.get("CONFLUENCE_POLL_LEASE_TTL_SECONDS") or "300")
    initial_lookback = str(values.get("CONFLUENCE_POLL_INITIAL_LOOKBACK") or "PT1H")
    max_event_attempts = int(values.get("CONFLUENCE_POLL_MAX_EVENT_ATTEMPTS") or "3")
    dry_run_raw = str(values.get("CONFLUENCE_POLL_DRY_RUN") or "").strip().lower()
    dry_run = dry_run_raw in {"1", "true", "yes", "on"}
    space_keys_raw = str(values.get("CONFLUENCE_POLL_SPACE_KEYS") or "").strip()
    space_keys = tuple(x.strip() for x in space_keys_raw.split(",") if x.strip())
    strategy_raw = (
        str(values.get("CONFLUENCE_ASSESSMENT_STRATEGY") or "single_pass").strip().lower()
    )
    assessment_strategy = "per_control" if strategy_raw == "per_control" else "single_pass"
    return PollerConfig(
        poll_interval_seconds=max(1, poll_interval_seconds),
        lease_ttl_seconds=max(10, lease_ttl_seconds),
        initial_lookback=initial_lookback,
        max_event_attempts=max(1, max_event_attempts),
        dry_run=dry_run,
        space_keys=space_keys,
        assessment_strategy=assessment_strategy,
    )


def run_forever_from_env(env: dict[str, str] | None = None) -> None:
    """Run run forever from env."""
    values = dict(os.environ) if env is None else dict(env)
    config = load_poller_config_from_env(values)
    state_store = create_cosmos_state_store_from_env(values)
    server = create_confluence_mcp_server_from_env(values)
    adapter = create_orchestrator_adapter_from_env(values)
    run_forever(config, state_store=state_store, server=server, adapter=adapter)


__all__ = [
    "create_cosmos_state_store_from_env",
    "load_poller_config_from_env",
    "run_forever",
    "run_poll_cycle",
]
