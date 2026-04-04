from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any, Callable, Iterable

from azure.identity import DefaultAzureCredential

from .intake import build_assessment_job_from_provider_event
from .interfaces import OrchestratorAdapter
from .mcp.confluence import ConfluenceMCPServer
from .runtime_wiring import create_confluence_mcp_server_from_env, create_orchestrator_adapter_from_env
from .state_store import CosmosPollingStateStore, PollingStateStore


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str, str]:
    occurred_at = str(event.get("occurred_at") or "")
    title = str(event.get("title") or "")
    event_id = str(event.get("event_id") or "")
    return (occurred_at, title.lower(), event_id)


def _render_assessment_comment(assessment: dict[str, Any]) -> str:
    summary = str(assessment.get("executive_summary") or "Assessment completed.")
    findings = list(assessment.get("findings") or [])
    overall_risk = str(assessment.get("overall_risk_rating") or "unknown").replace("_", " ").title()
    recommended_actions = list(assessment.get("recommended_actions") or [])
    missing_evidence = list(assessment.get("missing_evidence") or [])
    citations = list(assessment.get("citations") or [])

    parts = [
        "<p><strong>Automated compliance review</strong></p>",
        f"<p><strong>Overall risk:</strong> {escape(overall_risk)}</p>",
        f"<p>{escape(summary)}</p>",
        f"<p><strong>Findings:</strong> {len(findings)}</p>",
    ]

    if findings:
        parts.append("<p><strong>Key findings</strong></p>")
        parts.append("<ul>")
        for finding in findings[:5]:
            requirement_id = escape(str(finding.get("requirement_id") or "unknown"))
            framework = escape(str(finding.get("framework") or "Unknown"))
            status = escape(str(finding.get("status") or "unknown").replace("_", " "))
            severity = escape(str(finding.get("severity") or "unknown"))
            rationale = escape(str(finding.get("rationale") or "No rationale provided."))
            parts.append(
                "<li>"
                f"<strong>{requirement_id}</strong> ({framework}; {status}; severity {severity})"
                f"<br/>{rationale}"
            )
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
                    parts.append(f"<li><strong>Evidence:</strong> {escape(', '.join(evidence_sources))}</li>")
                for gap in gaps[:3]:
                    parts.append(f"<li><strong>Gap:</strong> {gap}</li>")
                for recommendation in recommendations[:3]:
                    parts.append(f"<li><strong>Recommendation:</strong> {recommendation}</li>")
                parts.append("</ul>")
            parts.append("</li>")
        parts.append("</ul>")

    if missing_evidence:
        parts.append("<p><strong>Missing evidence</strong></p>")
        parts.append("<ul>")
        for item in missing_evidence[:5]:
            parts.append(f"<li>{escape(str(item))}</li>")
        parts.append("</ul>")

    if recommended_actions:
        parts.append("<p><strong>Recommended actions</strong></p>")
        parts.append("<ul>")
        for item in recommended_actions[:5]:
            parts.append(f"<li>{escape(str(item))}</li>")
        parts.append("</ul>")

    if citations:
        parts.append("<p><strong>Citations</strong></p>")
        parts.append("<ul>")
        for item in citations[:5]:
            parts.append(f"<li>{escape(str(item))}</li>")
        parts.append("</ul>")

    return "".join(parts)


@dataclass(frozen=True)
class PollerConfig:
    source: str = "confluence"
    poll_interval_seconds: int = 75
    lease_ttl_seconds: int = 300
    initial_lookback: str = "PT1H"
    max_event_attempts: int = 3
    dry_run: bool = False
    space_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PollCycleResult:
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
    scope = {"space_keys": list(space_keys)} if list(space_keys) else None
    result = server.get_recent_mentions(since=since_iso, scope_filter=scope)
    mentions = list(result.get("mentions") or [])
    bounded: list[dict[str, Any]] = []
    for mention in mentions:
        occurred_at = str(mention.get("occurred_at") or "")
        if not occurred_at:
            continue
        if _parse_iso(occurred_at) <= window_end:
            bounded.append(mention)
    bounded.sort(key=_event_sort_key)
    return bounded


def _process_assessment_event(
    *,
    adapter: OrchestratorAdapter,
    server: ConfluenceMCPServer,
    event: dict[str, Any],
    dry_run: bool,
) -> None:
    target_url = str(event.get("target_url") or "")
    target_id = str(event.get("target_id") or "")
    if not target_url or not target_id:
        raise ValueError(f"Event missing target reference fields: {event}")

    job = build_assessment_job_from_provider_event(
        {
            "event_id": event.get("event_id") or "",
            "target_id": target_id,
            "target_url": target_url,
            "trigger_type": event.get("trigger_type") or "mention",
            "requester_id": event.get("mentioner_account_id") or "",
        },
        provider_hint="confluence",
        request_identity_mode="app_only",
        delivery_policy="inline_else_email",
    )
    assessment = adapter.run_assessment(job)
    if dry_run:
        return

    comment_body = _render_assessment_comment(assessment)
    idempotency_key = str(event.get("event_id") or job.correlation_id)
    delivery = server.post_comment(
        target_id,
        comment_body=comment_body,
        identity_mode="app_only",
        idempotency_key=idempotency_key,
    )
    if not delivery.success:
        raise RuntimeError(f"Failed posting Confluence comment for event {idempotency_key}: {delivery.failures}")


def run_poll_cycle(
    *,
    config: PollerConfig,
    state_store: PollingStateStore,
    server: ConfluenceMCPServer,
    adapter: OrchestratorAdapter,
    process_event: Callable[[dict[str, Any]], None] | None = None,
) -> PollCycleResult:
    run_id = str(uuid.uuid4())
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
    try:
        state = state_store.load_state(config.source)
        now = _now_utc()
        window_end = now
        since_iso = state.watermark or _iso(now - timedelta(hours=1))

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
                event=event,
                dry_run=config.dry_run,
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
    finally:
        state_store.release_lease(config.source, owner_run_id=run_id)


def run_forever(config: PollerConfig, *, state_store: PollingStateStore, server: ConfluenceMCPServer, adapter: OrchestratorAdapter) -> None:
    while True:
        run_poll_cycle(config=config, state_store=state_store, server=server, adapter=adapter)
        time.sleep(max(1, config.poll_interval_seconds))


def create_cosmos_state_store_from_env(env: dict[str, str] | None = None) -> CosmosPollingStateStore:
    values = dict(os.environ) if env is None else dict(env)
    endpoint = str(values.get("AZURE_COSMOS_ENDPOINT") or "").strip()
    database_name = str(values.get("AZURE_COSMOS_DATABASE_NAME") or "").strip()
    container_name = str(values.get("AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME") or "orchestration-state").strip()
    if not endpoint or not database_name:
        raise ValueError("AZURE_COSMOS_ENDPOINT and AZURE_COSMOS_DATABASE_NAME are required")

    from azure.cosmos import CosmosClient

    credential = DefaultAzureCredential()
    client = CosmosClient(url=endpoint, credential=credential)
    db = client.get_database_client(database_name)
    container = db.get_container_client(container_name)
    return CosmosPollingStateStore(container)


def load_poller_config_from_env(env: dict[str, str] | None = None) -> PollerConfig:
    values = dict(os.environ) if env is None else dict(env)
    poll_interval_seconds = int(values.get("CONFLUENCE_POLL_INTERVAL_SECONDS") or "75")
    lease_ttl_seconds = int(values.get("CONFLUENCE_POLL_LEASE_TTL_SECONDS") or "300")
    initial_lookback = str(values.get("CONFLUENCE_POLL_INITIAL_LOOKBACK") or "PT1H")
    max_event_attempts = int(values.get("CONFLUENCE_POLL_MAX_EVENT_ATTEMPTS") or "3")
    dry_run_raw = str(values.get("CONFLUENCE_POLL_DRY_RUN") or "").strip().lower()
    dry_run = dry_run_raw in {"1", "true", "yes", "on"}
    space_keys_raw = str(values.get("CONFLUENCE_POLL_SPACE_KEYS") or "").strip()
    space_keys = tuple(x.strip() for x in space_keys_raw.split(",") if x.strip())
    return PollerConfig(
        poll_interval_seconds=max(1, poll_interval_seconds),
        lease_ttl_seconds=max(10, lease_ttl_seconds),
        initial_lookback=initial_lookback,
        max_event_attempts=max(1, max_event_attempts),
        dry_run=dry_run,
        space_keys=space_keys,
    )


def run_forever_from_env(env: dict[str, str] | None = None) -> None:
    values = dict(os.environ) if env is None else dict(env)
    config = load_poller_config_from_env(values)
    state_store = create_cosmos_state_store_from_env(values)
    server = create_confluence_mcp_server_from_env(values)
    adapter = create_orchestrator_adapter_from_env(values)
    run_forever(config, state_store=state_store, server=server, adapter=adapter)
