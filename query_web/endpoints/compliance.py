"""Compliance report endpoints and job tracking."""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from query_web.constants import COMPLIANCE_REPORT_SCHEMA_VERSION
from query_web.utils import _utc_now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class ComplianceReportRequest(BaseModel):
    """Request payload for corpus-based compliance report generation."""

    question: str = ""
    retrieve_k: int = Field(default=5, ge=1, le=20)
    controls_top_k: int = Field(default=4, ge=1, le=2000)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    corpus_b_upload_batch: str | None = None
    corpus_c_upload_batch: str | None = None
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    assessment_strategy: Literal["single_pass", "per_control"] = "single_pass"
    validation_mode: Literal["hard", "soft"] = "hard"
    auth_token: str = ""


class AzureComplianceReportRequest(BaseModel):
    """Request payload for Azure resource compliance assessment generation."""

    subscription_id: str
    resource_group: str
    resource_ids: list[str] = Field(default_factory=list)
    controls_framework: str = "NIST CSF"
    controls_top_k: int = Field(default=4, ge=1, le=2000)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    assessment_strategy: Literal["single_pass", "per_control"] = "single_pass"
    validation_mode: Literal["hard", "soft"] = "hard"
    auth_token: str = ""


class ComplianceFinding(BaseModel):
    """Single normalised compliance finding in the structured report schema."""

    finding_id: str = Field(min_length=1, max_length=64)
    requirement_id: str = Field(min_length=1, max_length=128)
    framework: str = Field(min_length=1, max_length=64)
    status: Literal[
        "compliant",
        "partially_compliant",
        "non_compliant",
        "not_applicable",
        "insufficient_evidence",
    ]
    severity: Literal["low", "medium", "high", "critical"]
    rationale: str = Field(min_length=1, max_length=3000)
    evidence_sources: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=20)


class ComplianceReportStructured(BaseModel):
    """Structured compliance report payload returned to clients and exports."""

    schema_version: str = Field(min_length=1, max_length=32)
    executive_summary: str = Field(min_length=1, max_length=3000)
    scope_and_inputs: list[str] = Field(default_factory=list, min_length=1, max_length=40)
    controls_assessed: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    guidance_applied: list[str] = Field(default_factory=list, max_length=80)
    findings: list[ComplianceFinding] = Field(default_factory=list, min_length=1, max_length=300)
    overall_risk_rating: Literal["low", "medium", "high", "critical"]
    missing_evidence: list[str] = Field(default_factory=list, max_length=80)
    recommended_actions: list[str] = Field(default_factory=list, min_length=1, max_length=80)
    citations: list[str] = Field(default_factory=list, min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# In-memory async job tracking
# ---------------------------------------------------------------------------


@dataclass
class _ReportJob:
    job_id: str
    kind: Literal["compliance", "azure"]
    created_at: str
    updated_at: str
    state: Literal["queued", "running", "completed", "failed"] = "queued"
    message: str = "Queued"
    total_controls: int = 0
    completed_controls: int = 0
    current_requirement_id: str = ""
    result: dict[str, Any] | None = None
    error: str = ""


_REPORT_JOBS: dict[str, _ReportJob] = {}
_REPORT_JOBS_LOCK = threading.Lock()


def _new_report_job(kind: Literal["compliance", "azure"]) -> _ReportJob:
    now = _utc_now_iso()
    job = _ReportJob(
        job_id=str(uuid.uuid4()),
        kind=kind,
        created_at=now,
        updated_at=now,
    )
    with _REPORT_JOBS_LOCK:
        _REPORT_JOBS[job.job_id] = job
    return job


def _get_report_job(job_id: str) -> _ReportJob | None:
    with _REPORT_JOBS_LOCK:
        return _REPORT_JOBS.get(job_id)


def _update_report_job(job_id: str, **updates: Any) -> None:
    with _REPORT_JOBS_LOCK:
        job = _REPORT_JOBS.get(job_id)
        if not job:
            return
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = _utc_now_iso()


# ---------------------------------------------------------------------------
# Compliance pipeline constants
# ---------------------------------------------------------------------------

COMPLIANCE_REPORT_PROMPT = (
    "You are a compliance assessment assistant. Build a strict JSON compliance report "
    "using Corpus A and Corpus B as grounding data, and Corpus C as assessed artifacts. "
    "Do not invent requirements or evidence. If evidence is missing, state it explicitly. "
    "Return JSON only. No markdown, no prose outside JSON, and no code fences."
)

COMPLIANCE_REPORT_JSON_SCHEMA_HINT = (
    "Required JSON shape:\n"
    "{\n"
    "  \"schema_version\": string (must be 'v1.1'),\n"
    '  "executive_summary": string,\n'
    '  "scope_and_inputs": string[],\n'
    '  "controls_assessed": string[],\n'
    '  "guidance_applied": string[],\n'
    '  "findings": [\n'
    "    {\n"
    '      "finding_id": string,\n'
    '      "requirement_id": string,\n'
    '      "framework": string,\n'
    '      "status": "compliant"|"partially_compliant"|"non_compliant"|"not_applicable"|"insufficient_evidence",\n'
    '      "severity": "low"|"medium"|"high"|"critical",\n'
    '      "rationale": string,\n'
    '      "evidence_sources": string[],\n'
    '      "gaps": string[],\n'
    '      "recommendations": string[]\n'
    "    }\n"
    "  ],\n"
    '  "overall_risk_rating": "low"|"medium"|"high"|"critical",\n'
    '  "missing_evidence": string[],\n'
    '  "recommended_actions": string[],\n'
    '  "citations": string[]\n'
    "}"
)


# ---------------------------------------------------------------------------
# Compliance pipeline helpers
# ---------------------------------------------------------------------------


def _extract_json_object(text: str, svc: Any) -> dict[str, Any]:
    cleaned = svc._unwrap_answer(text).strip()
    if not cleaned:
        raise ValueError("Model returned empty response")

    try:
        import json as _json

        parsed = _json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    import json as _json

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("Model response did not contain a JSON object")

    parsed = _json.loads(cleaned[first : last + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON payload is not an object")
    return parsed


def _validate_compliance_report_payload(payload: dict[str, Any]) -> ComplianceReportStructured:
    report = ComplianceReportStructured.model_validate(payload)
    if report.schema_version != COMPLIANCE_REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {COMPLIANCE_REPORT_SCHEMA_VERSION}, got {report.schema_version}"
        )
    return report


def _clean_non_empty_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if text:
            items.append(text)
    return items


def _control_terms(control: dict[str, Any]) -> set[str]:
    text = " ".join(
        [
            str(control.get("requirement_id") or ""),
            str(control.get("control_family") or ""),
            str(control.get("requirement_text") or ""),
            str(control.get("guidance_text") or ""),
        ]
    ).lower()
    return {token for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text) if len(token) >= 4}


def _select_chunks_for_control(
    control: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    max_chunks: int,
) -> list[dict[str, Any]]:
    if not chunks:
        return []

    terms = _control_terms(control)
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        lower_content = content.lower()
        overlap = sum(1 for term in terms if term in lower_content)
        score = float(chunk.get("score") or 0.0)
        scored.append((overlap, score, chunk))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:max_chunks]]


def _build_compliance_scope_inputs(
    *,
    question: str | None = None,
    controls_count: int,
    corpus_b_chunk_count: int,
    corpus_c_chunk_count: int,
    corpus_b_indexed_total: int,
    corpus_c_indexed_total: int,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    corpus_c_scope_label: str = "Corpus C artifacts retrieved",
    assessment_strategy: str | None = None,
) -> list[str]:
    items: list[str] = []
    if question is not None:
        items.append(f"Assessment question: {question[:200]}")
    items.extend(
        [
            f"Corpus A controls retrieved: {controls_count}",
            f"Corpus B guidance retrieved: {corpus_b_chunk_count}",
            f"{corpus_c_scope_label}: {corpus_c_chunk_count}",
        ]
    )
    if corpus_b_indexed_total > 0:
        items.append(f"Corpus B indexed documents available: {corpus_b_indexed_total}")
    if corpus_c_indexed_total > 0:
        items.append(f"Corpus C indexed documents available: {corpus_c_indexed_total}")
    if corpus_b_upload_batch:
        matched = corpus_b_filtered_total if corpus_b_filtered_total is not None else "unknown"
        items.append(
            f"Corpus B upload batch filter active: {corpus_b_upload_batch} (indexed matches: {matched})"
        )
    if corpus_c_upload_batch:
        matched = corpus_c_filtered_total if corpus_c_filtered_total is not None else "unknown"
        items.append(
            f"Corpus C upload batch filter active: {corpus_c_upload_batch} (indexed matches: {matched})"
        )
    if assessment_strategy and assessment_strategy != "single_pass":
        items.append(f"Assessment strategy: {assessment_strategy}")
    return items


def _normalise_compliance_report_payload(
    payload: dict[str, Any],
    *,
    svc: Any,
    question: str,
    controls: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    corpus_b_indexed_total: int = 0,
    corpus_c_indexed_total: int = 0,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    assessment_strategy: str | None = None,
) -> dict[str, Any]:
    report = dict(payload or {})

    controls_count = len(controls)
    corpus_b_count = len(corpus_b_chunks)
    corpus_c_count = len(corpus_c_chunks)

    control_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in controls
        if str(item.get("requirement_id") or "").strip()
    ]
    control_frameworks = [
        str(item.get("framework") or "").strip()
        for item in controls
        if str(item.get("framework") or "").strip()
    ]
    source_names = [
        svc._chunk_reference_label(item, fallback="")
        for item in [*corpus_c_chunks, *corpus_b_chunks]
        if svc._chunk_reference_label(item, fallback="")
    ]

    default_requirement_id = control_ids[0] if control_ids else "UNMAPPED"
    default_framework = control_frameworks[0] if control_frameworks else "Unknown"
    default_sources = source_names or ["No direct evidence source available"]

    report["schema_version"] = str(
        report.get("schema_version") or COMPLIANCE_REPORT_SCHEMA_VERSION
    ).strip()

    executive_summary = str(report.get("executive_summary") or "").strip()
    if not executive_summary:
        executive_summary = (
            "Automated compliance assessment generated with available grounded evidence. "
            "Some required fields were normalised due to incomplete model output."
        )
    if (
        controls_count > 0 or corpus_c_count > 0
    ) and "no normative requirements" in executive_summary.lower():
        executive_summary = (
            "Automated compliance assessment generated from retrieved corpus evidence. "
            "Some model statements were corrected to match retrieved control and artifact counts."
        )
    report["executive_summary"] = executive_summary

    scope_and_inputs = _build_compliance_scope_inputs(
        controls_count=controls_count,
        corpus_b_chunk_count=corpus_b_count,
        corpus_c_chunk_count=corpus_c_count,
        corpus_b_indexed_total=corpus_b_indexed_total,
        corpus_c_indexed_total=corpus_c_indexed_total,
        corpus_b_upload_batch=corpus_b_upload_batch,
        corpus_c_upload_batch=corpus_c_upload_batch,
        corpus_b_filtered_total=corpus_b_filtered_total,
        corpus_c_filtered_total=corpus_c_filtered_total,
        assessment_strategy=assessment_strategy,
    )
    report["scope_and_inputs"] = scope_and_inputs

    controls_assessed = _clean_non_empty_string_list(report.get("controls_assessed"))
    if control_ids:
        controls_assessed = control_ids
    elif not controls_assessed:
        controls_assessed = ["UNMAPPED"]
    report["controls_assessed"] = controls_assessed

    report["guidance_applied"] = _clean_non_empty_string_list(report.get("guidance_applied"))

    findings_raw = report.get("findings")
    findings: list[dict[str, Any]] = []
    if isinstance(findings_raw, list):
        findings = [item for item in findings_raw if isinstance(item, dict)]

    if not findings:
        findings = [
            {
                "finding_id": "finding-1",
                "requirement_id": default_requirement_id,
                "framework": default_framework,
                "status": "insufficient_evidence",
                "severity": "medium",
                "rationale": "Insufficient grounded evidence was available to produce a complete structured finding.",
                "evidence_sources": default_sources,
                "gaps": ["Insufficient structured evidence"],
                "recommendations": ["Collect additional evidence and re-run assessment."],
            }
        ]

    normalised_findings: list[dict[str, Any]] = []
    valid_status = {
        "compliant",
        "partially_compliant",
        "non_compliant",
        "not_applicable",
        "insufficient_evidence",
    }
    valid_severity = {"low", "medium", "high", "critical"}
    for idx, finding in enumerate(findings):
        finding_id = str(finding.get("finding_id") or "").strip() or f"finding-{idx + 1}"
        requirement_id = str(finding.get("requirement_id") or "").strip() or default_requirement_id
        framework = str(finding.get("framework") or "").strip() or default_framework
        status = str(finding.get("status") or "").strip().lower()
        severity = str(finding.get("severity") or "").strip().lower()
        rationale = str(finding.get("rationale") or "").strip()
        if not rationale:
            rationale = "Model output omitted rationale; marked as insufficient evidence."
            status = "insufficient_evidence"
        if status not in valid_status:
            status = "insufficient_evidence"
        if severity not in valid_severity:
            severity = "medium"

        evidence_sources = _clean_non_empty_string_list(finding.get("evidence_sources"))
        if not evidence_sources:
            evidence_sources = default_sources

        gaps = _clean_non_empty_string_list(finding.get("gaps"))
        recommendations = _clean_non_empty_string_list(finding.get("recommendations"))

        normalised_findings.append(
            {
                "finding_id": finding_id,
                "requirement_id": requirement_id,
                "framework": framework,
                "status": status,
                "severity": severity,
                "rationale": rationale,
                "evidence_sources": evidence_sources,
                "gaps": gaps,
                "recommendations": recommendations,
            }
        )
    report["findings"] = normalised_findings

    risk = str(report.get("overall_risk_rating") or "").strip().lower()
    if risk not in {"low", "medium", "high", "critical"}:
        risk = "medium"
    report["overall_risk_rating"] = risk

    missing_evidence = _clean_non_empty_string_list(report.get("missing_evidence"))
    if not missing_evidence and (not controls or not source_names):
        missing_evidence = [
            "Insufficient grounded evidence for full control mapping and source attribution.",
        ]
    report["missing_evidence"] = missing_evidence

    recommended_actions = _clean_non_empty_string_list(report.get("recommended_actions"))
    if not recommended_actions:
        recommended_actions = [
            "Collect additional evidence and re-run compliance assessment.",
        ]
    report["recommended_actions"] = recommended_actions

    citations = _clean_non_empty_string_list(report.get("citations"))
    if not citations:
        citations = default_sources
    report["citations"] = citations

    return report


def _report_findings_to_csv(report: ComplianceReportStructured) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "finding_id",
            "requirement_id",
            "framework",
            "status",
            "severity",
            "rationale",
            "evidence_sources",
            "gaps",
            "recommendations",
        ]
    )
    for finding in report.findings:
        writer.writerow(
            [
                finding.finding_id,
                finding.requirement_id,
                finding.framework,
                finding.status,
                finding.severity,
                finding.rationale,
                " | ".join(finding.evidence_sources),
                " | ".join(finding.gaps),
                " | ".join(finding.recommendations),
            ]
        )
    return buffer.getvalue()


def _build_fallback_compliance_report_payload(
    *,
    svc: Any,
    question: str,
    controls: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    validation_error: str,
    corpus_b_indexed_total: int = 0,
    corpus_c_indexed_total: int = 0,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    assessment_strategy: str | None = None,
) -> dict[str, Any]:
    control_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in controls
        if str(item.get("requirement_id") or "").strip()
    ]
    framework_names = [
        str(item.get("framework") or "").strip()
        for item in controls
        if str(item.get("framework") or "").strip()
    ]
    evidence_sources = [
        svc._chunk_reference_label(item, fallback="")
        for item in [*corpus_c_chunks, *corpus_b_chunks]
        if svc._chunk_reference_label(item, fallback="")
    ]

    return {
        "schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "executive_summary": (
            "Fallback compliance report generated because the model returned an unusable or invalid response. "
            "Retrieved evidence counts are preserved below."
        ),
        "scope_and_inputs": _build_compliance_scope_inputs(
            question=question,
            controls_count=len(controls),
            corpus_b_chunk_count=len(corpus_b_chunks),
            corpus_c_chunk_count=len(corpus_c_chunks),
            corpus_b_indexed_total=corpus_b_indexed_total,
            corpus_c_indexed_total=corpus_c_indexed_total,
            corpus_b_upload_batch=corpus_b_upload_batch,
            corpus_c_upload_batch=corpus_c_upload_batch,
            corpus_b_filtered_total=corpus_b_filtered_total,
            corpus_c_filtered_total=corpus_c_filtered_total,
            assessment_strategy=assessment_strategy,
        ),
        "controls_assessed": control_ids or ["UNMAPPED"],
        "guidance_applied": evidence_sources[:10],
        "findings": [
            {
                "finding_id": "fallback-1",
                "requirement_id": control_ids[0] if control_ids else "UNMAPPED",
                "framework": framework_names[0] if framework_names else "Unknown",
                "status": "insufficient_evidence",
                "severity": "medium",
                "rationale": (
                    "The model response could not be validated; this fallback finding preserves the retrieved evidence "
                    f"state instead. Validation error: {validation_error}"
                )[:3000],
                "evidence_sources": evidence_sources or ["No evidence sources retrieved"],
                "gaps": ["Model output failed schema validation"],
                "recommendations": ["Review retrieved sources and re-run the report."],
            }
        ],
        "overall_risk_rating": "medium",
        "missing_evidence": [
            "Additional corroborating evidence may be required for a final compliance conclusion."
        ],
        "recommended_actions": [
            "Retry the report or review source grounding manually.",
        ],
        "citations": evidence_sources or ["No evidence sources retrieved"],
    }


def _assess_control_finding_with_llm(
    *,
    svc: Any,
    question: str,
    control: dict[str, Any],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    temperature: float,
) -> dict[str, Any]:
    requirement_id = str(control.get("requirement_id") or "").strip() or "UNMAPPED"
    framework = str(control.get("framework") or "").strip() or "Unknown"

    b_context = "\n\n".join(
        f"Source: {svc._chunk_reference_label(c, fallback='guidance')}\nExcerpt: {svc.sanitise_untrusted_text(str(c.get('content') or '')[:900])}"
        for c in corpus_b_chunks
    )
    c_context = "\n\n".join(
        f"Source: {svc._chunk_reference_label(c, fallback='artifact')}\nExcerpt: {svc.sanitise_untrusted_text(str(c.get('content') or '')[:1200])}"
        for c in corpus_c_chunks
    )

    messages = [
        {"role": "system", "content": svc.PROMPT_INJECTION_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Assess one compliance control and return exactly one JSON finding object with fields: "
                "finding_id, requirement_id, framework, status, severity, rationale, evidence_sources, gaps, recommendations."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Assessment question:\n{svc.sanitise_untrusted_text(question)}\n\n"
                "Control under assessment:\n"
                f"Requirement ID: {requirement_id}\n"
                f"Framework: {framework}\n"
                f"Control Family: {control.get('control_family', '')}\n"
                f"Requirement: {svc.sanitise_untrusted_text(str(control.get('requirement_text') or '')[:1600])}\n"
                f"Guidance: {svc.sanitise_untrusted_text(str(control.get('guidance_text') or '')[:1000])}\n\n"
                f"Corpus B guidance (optional):\n{b_context or 'No relevant Corpus B guidance.'}\n\n"
                f"Corpus C evidence:\n{c_context or 'No relevant Corpus C evidence.'}\n\n"
                "Constraints:\n"
                "- status must be one of compliant|partially_compliant|non_compliant|not_applicable|insufficient_evidence\n"
                "- severity must be one of low|medium|high|critical\n"
                "- include at least one evidence source when possible\n"
                "- return JSON object only"
            ),
        },
    ]

    try:
        raw = svc._chat_completion_with_empty_retry(
            messages,
            deployment=svc.config.query_deployment,
            temperature=temperature,
        )
        parsed = _extract_json_object(raw, svc)
    except Exception:
        parsed = {}

    fallback = {
        "finding_id": f"finding-{requirement_id}",
        "requirement_id": requirement_id,
        "framework": framework,
        "status": "insufficient_evidence",
        "severity": "medium",
        "rationale": "Insufficient evidence for deterministic assessment in per-control mode.",
        "evidence_sources": [
            svc._chunk_reference_label(item, fallback="evidence")
            for item in (corpus_c_chunks or corpus_b_chunks)[:3]
        ]
        or ["No evidence sources retrieved"],
        "gaps": ["Additional artifact evidence needed for this control."],
        "recommendations": ["Provide corroborating evidence and reassess this control."],
    }
    fallback.update(parsed)

    fallback["evidence_sources"] = _clean_non_empty_string_list(
        fallback.get("evidence_sources")
    ) or ["No evidence sources retrieved"]
    fallback["gaps"] = _clean_non_empty_string_list(fallback.get("gaps"))
    fallback["recommendations"] = _clean_non_empty_string_list(fallback.get("recommendations"))
    return fallback


def _build_per_control_report_payload(
    *,
    svc: Any,
    question: str,
    controls: list[dict[str, Any]],
    corpus_b_chunks: list[dict[str, Any]],
    corpus_c_chunks: list[dict[str, Any]],
    temperature: float,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
    corpus_b_indexed_total: int = 0,
    corpus_c_indexed_total: int = 0,
    corpus_b_upload_batch: str | None = None,
    corpus_c_upload_batch: str | None = None,
    corpus_b_filtered_total: int | None = None,
    corpus_c_filtered_total: int | None = None,
    corpus_c_scope_label: str = "Corpus C artifacts retrieved",
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    total = len(controls)

    for index, control in enumerate(controls, start=1):
        requirement_id = str(control.get("requirement_id") or "").strip() or f"CTRL-{index}"
        if progress_cb:
            progress_cb(index - 1, total, requirement_id, f"Assessing control {index}/{total}")

        relevant_b = _select_chunks_for_control(control, corpus_b_chunks, max_chunks=2)
        relevant_c = _select_chunks_for_control(control, corpus_c_chunks, max_chunks=3)
        finding = _assess_control_finding_with_llm(
            svc=svc,
            question=question,
            control=control,
            corpus_b_chunks=relevant_b,
            corpus_c_chunks=relevant_c,
            temperature=temperature,
        )
        findings.append(finding)
        if progress_cb:
            progress_cb(index, total, requirement_id, f"Completed control {index}/{total}")

    scope_inputs = _build_compliance_scope_inputs(
        question=question,
        controls_count=len(controls),
        corpus_b_chunk_count=len(corpus_b_chunks),
        corpus_c_chunk_count=len(corpus_c_chunks),
        corpus_b_indexed_total=corpus_b_indexed_total,
        corpus_c_indexed_total=corpus_c_indexed_total,
        corpus_b_upload_batch=corpus_b_upload_batch,
        corpus_c_upload_batch=corpus_c_upload_batch,
        corpus_b_filtered_total=corpus_b_filtered_total,
        corpus_c_filtered_total=corpus_c_filtered_total,
        corpus_c_scope_label=corpus_c_scope_label,
        assessment_strategy="per_control",
    )
    control_ids = [
        str(c.get("requirement_id") or "").strip()
        for c in controls
        if str(c.get("requirement_id") or "").strip()
    ]
    source_names = [
        svc._chunk_reference_label(item, fallback="")
        for item in [*corpus_b_chunks, *corpus_c_chunks]
        if svc._chunk_reference_label(item, fallback="")
    ]

    statuses = [str(item.get("status") or "").strip().lower() for item in findings]
    if any(s in {"non_compliant", "critical"} for s in statuses):
        risk = "high"
    elif any(s in {"partially_compliant", "insufficient_evidence"} for s in statuses):
        risk = "medium"
    else:
        risk = "low"

    missing_evidence = [
        f"Control {item.get('requirement_id', '')}: additional corroborating evidence required"
        for item in findings
        if str(item.get("status") or "").strip().lower()
        in {"insufficient_evidence", "partially_compliant"}
    ][:40]

    return {
        "schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "executive_summary": (
            "Per-control compliance assessment completed. Findings are generated sequentially per retrieved control "
            "to improve coverage breadth over single-pass context windows."
        ),
        "scope_and_inputs": scope_inputs,
        "controls_assessed": control_ids or ["UNMAPPED"],
        "guidance_applied": source_names[:20],
        "findings": findings,
        "overall_risk_rating": risk,
        "missing_evidence": missing_evidence,
        "recommended_actions": [
            "Address non-compliant and partially compliant controls in priority order.",
            "Collect missing evidence for controls marked insufficient_evidence.",
        ],
        "citations": source_names[:40] or ["No evidence sources retrieved"],
    }


def _report_to_markdown(report: ComplianceReportStructured) -> str:
    lines: list[str] = []
    lines.append("# Compliance Report")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(report.executive_summary)
    lines.append("")
    lines.append("## Scope and Inputs")
    for item in report.scope_and_inputs:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Controls Assessed")
    for control in report.controls_assessed:
        lines.append(f"- {control}")
    lines.append("")
    lines.append("## Guidance Applied")
    if report.guidance_applied:
        for item in report.guidance_applied:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Findings")
    for finding in report.findings:
        lines.append(f"### {finding.finding_id} - {finding.requirement_id}")
        lines.append(f"- Framework: {finding.framework}")
        lines.append(f"- Status: {finding.status}")
        lines.append(f"- Severity: {finding.severity}")
        lines.append(f"- Rationale: {finding.rationale}")
        lines.append("- Evidence Sources:")
        for source in finding.evidence_sources:
            lines.append(f"  - {source}")
        lines.append("- Gaps:")
        if finding.gaps:
            for gap in finding.gaps:
                lines.append(f"  - {gap}")
        else:
            lines.append("  - None")
        lines.append("- Recommendations:")
        if finding.recommendations:
            for rec in finding.recommendations:
                lines.append(f"  - {rec}")
        else:
            lines.append("  - None")
        lines.append("")
    lines.append("## Overall Risk Rating")
    lines.append(report.overall_risk_rating)
    lines.append("")
    lines.append("## Missing Evidence")
    if report.missing_evidence:
        for item in report.missing_evidence:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Recommended Actions")
    for item in report.recommended_actions:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Citations")
    for item in report.citations:
        lines.append(f"- {item}")
    return "\n".join(lines)


def _chunk_azure_artifact(artifact: Any, chunk_size: int = 2000) -> list[dict[str, Any]]:
    content = (getattr(artifact, "content", None) or "").strip()
    title = str(getattr(artifact, "title", None) or "Azure scope evidence")
    if not content:
        return []
    chunks = []
    for i in range(0, len(content), chunk_size):
        chunk_text = content[i : i + chunk_size].strip()
        if chunk_text:
            chunks.append({"content": chunk_text, "source_name": title, "cosine_score": 1.0})
    return chunks


def generate_compliance_report_result(
    payload: ComplianceReportRequest,
    *,
    svc: Any,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    """Generate a compliance report using Corpus A/B/C retrieval and LLM synthesis."""

    question = payload.question.strip()
    effective_question = question
    if not effective_question:
        framework_hint = svc._canonical_framework_name(payload.controls_framework) or "selected"
        effective_question = (
            "Perform a general compliance assessment of Corpus C artifacts against "
            f"Corpus A {framework_hint} controls and Corpus B guidance."
        )

    controls, controls_timings = svc._controls_search(
        effective_question,
        retrieve_k=payload.controls_top_k,
        use_semantic=svc.config.controls_semantic_default,
        framework_filter_override=svc._normalise_framework_filter(payload.controls_framework),
        comparison_mode=svc._normalise_controls_comparison_mode(payload.controls_comparison_mode),
    )

    selected_evidence_corpora = ["b"]
    evidence_corpus_filter_expr = svc._build_evidence_corpus_filter(selected_evidence_corpora)
    include_corpus_b = True
    include_corpus_c = True

    corpus_b_filter = "corpus eq 'b'"
    corpus_b_filtered_total: int | None = None
    if include_corpus_b:
        corpus_b_filter_expr = corpus_b_filter
        corpus_b_indexed_total = svc._count_search_documents_total_by_filter(
            svc.search_client, filter_expr=corpus_b_filter
        )
        if payload.corpus_b_upload_batch:
            escaped_batch = payload.corpus_b_upload_batch.replace("'", "''")
            corpus_b_filter = f"{corpus_b_filter} and upload_batch eq '{escaped_batch}'"
            corpus_b_filter_expr = corpus_b_filter
            corpus_b_filtered_total = svc._count_search_documents_total_by_filter(
                svc.search_client, filter_expr=corpus_b_filter
            )
        corpus_b_chunks, b_timings = svc._hybrid_search(
            effective_question,
            retrieve_k=payload.retrieve_k,
            evidence_filter=corpus_b_filter,
        )
    else:
        corpus_b_indexed_total = 0
        corpus_b_chunks = []
        b_timings = {"search_s": 0.0}
        corpus_b_filter_expr = None

    corpus_c_filter = "corpus eq 'c'"
    corpus_c_filtered_total: int | None = None
    if include_corpus_c:
        corpus_c_filter_expr = corpus_c_filter
        corpus_c_indexed_total = svc._count_search_documents_total_by_filter(
            svc.search_client, filter_expr=corpus_c_filter
        )
        if payload.corpus_c_upload_batch:
            escaped_batch = payload.corpus_c_upload_batch.replace("'", "''")
            corpus_c_filter = f"{corpus_c_filter} and upload_batch eq '{escaped_batch}'"
            corpus_c_filter_expr = corpus_c_filter
            corpus_c_filtered_total = svc._count_search_documents_total_by_filter(
                svc.search_client, filter_expr=corpus_c_filter
            )
        corpus_c_chunks, c_timings = svc._hybrid_search(
            effective_question,
            retrieve_k=payload.retrieve_k,
            evidence_filter=corpus_c_filter,
        )
    else:
        corpus_c_indexed_total = 0
        corpus_c_chunks = []
        c_timings = {"search_s": 0.0}
        corpus_c_filter_expr = None

    strategy = payload.assessment_strategy
    used_fallback_payload = False
    if strategy == "per_control" and controls:
        report_payload = _build_per_control_report_payload(
            svc=svc,
            question=effective_question,
            controls=controls,
            corpus_b_chunks=corpus_b_chunks,
            corpus_c_chunks=corpus_c_chunks,
            temperature=payload.temperature,
            progress_cb=progress_cb,
            corpus_b_indexed_total=corpus_b_indexed_total,
            corpus_c_indexed_total=corpus_c_indexed_total,
            corpus_b_upload_batch=payload.corpus_b_upload_batch,
            corpus_c_upload_batch=payload.corpus_c_upload_batch,
            corpus_b_filtered_total=corpus_b_filtered_total,
            corpus_c_filtered_total=corpus_c_filtered_total,
        )
    else:
        controls_context = "\n\n".join(
            (
                f"Requirement ID: {c['requirement_id']}\n"
                f"Framework: {c['framework']} {c['framework_version']}\n"
                f"Control Family: {c['control_family']}\n"
                f"Requirement: {svc.sanitise_untrusted_text(c['requirement_text'][:1200])}\n"
                f"Guidance: {svc.sanitise_untrusted_text(c['guidance_text'][:800]) or 'No supplementary guidance is available for this control; assess solely against the requirement text above.'}"
            )
            for c in controls
        )

        corpus_b_context = "\n\n".join(
            (
                f"Source: {svc._chunk_reference_label(c)}\n"
                f"Excerpt: {svc.sanitise_untrusted_text(c['content'][:1500])}"
            )
            for c in corpus_b_chunks
        )

        corpus_c_context = "\n\n".join(
            (
                f"Source: {svc._chunk_reference_label(c)}\n"
                f"Excerpt: {svc.sanitise_untrusted_text(c['content'][:1500])}"
            )
            for c in corpus_c_chunks
        )

        messages = [
            {"role": "system", "content": COMPLIANCE_REPORT_PROMPT},
            {"role": "system", "content": svc.PROMPT_INJECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Assessment question:\n{svc.sanitise_untrusted_text(effective_question)}\n\n"
                    "Use the following corpora:\n"
                    f"Corpus A (normative requirements):\n{controls_context or 'No Corpus A controls retrieved.'}\n\n"
                    f"Corpus B (narrative guidance):\n{corpus_b_context or 'No Corpus B guidance retrieved.'}\n\n"
                    f"Corpus C (assessed artifacts):\n{corpus_c_context or 'No Corpus C artifacts retrieved.'}\n\n"
                    "Generate only JSON that matches this exact schema and constraints:\n"
                    f"{COMPLIANCE_REPORT_JSON_SCHEMA_HINT}\n\n"
                    "Rules:\n"
                    f"- Set schema_version to exactly {COMPLIANCE_REPORT_SCHEMA_VERSION}.\n"
                    "- Use requirement IDs from Corpus A in findings.requirement_id.\n"
                    "- Include evidence source names from Corpus B/C in findings.evidence_sources.\n"
                    "- If evidence is missing, use status=insufficient_evidence and document it in missing_evidence.\n"
                    "- Provide at least one finding and at least one citation.\n"
                    "- Return raw JSON object only."
                ),
            },
        ]

        model_response = svc._chat_completion_with_empty_retry(
            messages,
            deployment=svc.config.query_deployment,
            temperature=payload.temperature,
        )
        try:
            report_payload = _extract_json_object(model_response, svc)
            report_payload = _normalise_compliance_report_payload(
                report_payload,
                svc=svc,
                question=effective_question,
                controls=controls,
                corpus_b_chunks=corpus_b_chunks,
                corpus_c_chunks=corpus_c_chunks,
                corpus_b_indexed_total=corpus_b_indexed_total,
                corpus_c_indexed_total=corpus_c_indexed_total,
                corpus_b_upload_batch=payload.corpus_b_upload_batch,
                corpus_c_upload_batch=payload.corpus_c_upload_batch,
                corpus_b_filtered_total=corpus_b_filtered_total,
                corpus_c_filtered_total=corpus_c_filtered_total,
                assessment_strategy=strategy,
            )
        except Exception as exc:
            if payload.validation_mode == "hard":
                raise
            used_fallback_payload = True
            report_payload = _build_fallback_compliance_report_payload(
                svc=svc,
                question=effective_question,
                controls=controls,
                corpus_b_chunks=corpus_b_chunks,
                corpus_c_chunks=corpus_c_chunks,
                validation_error=str(exc),
                corpus_b_indexed_total=corpus_b_indexed_total,
                corpus_c_indexed_total=corpus_c_indexed_total,
                corpus_b_upload_batch=payload.corpus_b_upload_batch,
                corpus_c_upload_batch=payload.corpus_c_upload_batch,
                corpus_b_filtered_total=corpus_b_filtered_total,
                corpus_c_filtered_total=corpus_c_filtered_total,
                assessment_strategy=strategy,
            )

    validation_error = ""
    report_structured: ComplianceReportStructured | None = None
    report_markdown = ""
    report_csv = ""
    schema_valid = False

    try:
        report_structured = _validate_compliance_report_payload(report_payload)
        report_markdown = _report_to_markdown(report_structured)
        report_csv = _report_findings_to_csv(report_structured)
        schema_valid = not used_fallback_payload
    except Exception as exc:
        logger.exception("Compliance report schema validation failed: %s", exc)
        validation_error = "Compliance report schema validation failed."
        if payload.validation_mode == "hard":
            raise RuntimeError(
                f"Compliance report schema validation failed: {validation_error}"
            ) from exc
        fallback_payload = _build_fallback_compliance_report_payload(
            svc=svc,
            question=effective_question,
            controls=controls,
            corpus_b_chunks=corpus_b_chunks,
            corpus_c_chunks=corpus_c_chunks,
            validation_error=str(exc),
            corpus_b_indexed_total=corpus_b_indexed_total,
            corpus_c_indexed_total=corpus_c_indexed_total,
            corpus_b_upload_batch=payload.corpus_b_upload_batch,
            corpus_c_upload_batch=payload.corpus_c_upload_batch,
            corpus_b_filtered_total=corpus_b_filtered_total,
            corpus_c_filtered_total=corpus_c_filtered_total,
            assessment_strategy=strategy,
        )
        report_structured = _validate_compliance_report_payload(fallback_payload)
        report_markdown = _report_to_markdown(report_structured)
        report_csv = _report_findings_to_csv(report_structured)

    report_filename_base = f"compliance-report-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    return {
        "mode": "compliance-report",
        "assessment_strategy": strategy,
        "report": report_markdown,
        "report_markdown": report_markdown,
        "report_structured": report_structured.model_dump() if report_structured else None,
        "report_findings_csv": report_csv,
        "report_filename_base": report_filename_base,
        "report_schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "validation_mode": payload.validation_mode,
        "schema_valid": schema_valid,
        "validation_error": validation_error,
        "controls_count": len(controls),
        "corpus_b_count": len(corpus_b_chunks),
        "corpus_c_count": len(corpus_c_chunks),
        "corpus_b_indexed_total": corpus_b_indexed_total,
        "corpus_c_indexed_total": corpus_c_indexed_total,
        "corpus_b_upload_batch_filter": payload.corpus_b_upload_batch,
        "corpus_c_upload_batch_filter": payload.corpus_c_upload_batch,
        "assessment_question_supplied": bool(question),
        "effective_assessment_question": effective_question,
        "evidence_corpora_selected": selected_evidence_corpora,
        "audit": {
            "evidence_corpus_filter_expr": evidence_corpus_filter_expr,
            "corpus_b_filter_expr": corpus_b_filter_expr,
            "corpus_c_filter_expr": corpus_c_filter_expr,
        },
        "corpus_b_filtered_total": corpus_b_filtered_total,
        "corpus_c_filtered_total": corpus_c_filtered_total,
        "timings": {
            **controls_timings,
            "corpus_b_search_s": b_timings.get("search_s", 0.0),
            "corpus_c_search_s": c_timings.get("search_s", 0.0),
        },
    }


def generate_azure_compliance_report_result(
    payload: AzureComplianceReportRequest,
    *,
    svc: Any,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    """Generate a compliance report for Azure scope evidence and selected framework."""

    subscription_id = payload.subscription_id.strip()
    resource_group = payload.resource_group.strip()
    resource_ids = [item.strip() for item in payload.resource_ids if item.strip()]
    if not subscription_id:
        raise ValueError("subscription_id must not be empty")
    if not resource_group and not resource_ids:
        raise ValueError("resource_group is required when resource_ids are not supplied")

    framework = svc._canonical_framework_name(payload.controls_framework)
    if framework is None:
        raise ValueError("controls_framework must be a supported framework value")

    resolved_env = dict(os.environ)
    resolved_env["CONTROLS_TOP_K"] = str(payload.controls_top_k)

    validation_error = ""
    report_structured: ComplianceReportStructured | None = None
    report_markdown = ""
    report_csv = ""
    schema_valid = False
    report_payload: dict[str, Any]

    if payload.assessment_strategy == "per_control":
        if progress_cb:
            progress_cb(0, 1, "", "Collecting Azure scope evidence")
        artifact, grounding = svc.collect_azure_grounding(
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_ids=resource_ids,
            controls_framework=framework,
            env=resolved_env,
            credential=svc.credential,
        )
        controls = list(grounding.corpus_a_results)
        corpus_b_chunks = list(grounding.corpus_b_results)
        corpus_c_chunks = _chunk_azure_artifact(artifact)
        scope_desc = (
            f"Azure {framework} compliance assessment: "
            f"subscription={subscription_id}, resource_group={resource_group or ', '.join(resource_ids[:3])}"
        )
        if progress_cb:
            progress_cb(
                0, len(controls), "", f"Starting per-control assessment: {len(controls)} controls"
            )
        report_payload = _build_per_control_report_payload(
            svc=svc,
            question=scope_desc,
            controls=controls,
            corpus_b_chunks=corpus_b_chunks,
            corpus_c_chunks=corpus_c_chunks,
            temperature=payload.temperature,
            progress_cb=progress_cb,
            corpus_c_scope_label="Live Azure artifacts collected",
        )
    else:
        if progress_cb:
            progress_cb(0, 1, "", "Collecting Azure scope evidence")
        assessment = svc.run_azure_assessment(
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_ids=resource_ids,
            controls_framework=framework,
            env=resolved_env,
            credential=svc.credential,
        )
        if progress_cb:
            progress_cb(1, 1, "", "Rendering assessment report")
        report_payload = assessment

    try:
        report_structured = _validate_compliance_report_payload(report_payload)
        report_markdown = _report_to_markdown(report_structured)
        report_csv = _report_findings_to_csv(report_structured)
        schema_valid = True
    except Exception as exc:
        logger.exception("Azure compliance report schema validation failed: %s", exc)
        validation_error = "Compliance report schema validation failed."
        if payload.validation_mode == "hard":
            raise RuntimeError("Compliance report schema validation failed") from exc

    report_filename_base = f"azure-compliance-report-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    return {
        "mode": "azure-compliance-report",
        "assessment_strategy": payload.assessment_strategy,
        "framework": framework,
        "controls_top_k": payload.controls_top_k,
        "temperature": payload.temperature,
        "scope": {
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "resource_ids": resource_ids,
        },
        "report": report_markdown,
        "report_markdown": report_markdown,
        "report_structured": report_structured.model_dump() if report_structured else None,
        "report_findings_csv": report_csv,
        "report_filename_base": report_filename_base,
        "report_schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "validation_mode": payload.validation_mode,
        "schema_valid": schema_valid,
        "validation_error": validation_error,
    }


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def register_compliance_endpoints(app: Any, svc: Any) -> None:
    """Register compliance report endpoints.

    Parameters
    ----------
    app : FastAPI
        The application instance.
    svc : module
        Service container (the app module at runtime).  All helpers are accessed
        via ``svc.attribute`` at *call time* so that ``patch.object(svc, ...)``
        patches work correctly in tests.
    """

    @app.post("/api/compliance/report")
    def generate_compliance_report(
        request: Request, payload: ComplianceReportRequest
    ) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            corpus_c_base_filter = "corpus eq 'c'"
            corpus_c_indexed_total = svc._count_search_documents_total_by_filter(
                svc.search_client,
                filter_expr=corpus_c_base_filter,
            )
            if corpus_c_indexed_total <= 0:
                return JSONResponse(
                    {
                        "error": (
                            "Compliance report is unavailable because there are no Corpus C "
                            "documents to assess. Upload and index Corpus C artifacts first."
                        )
                    },
                    status_code=400,
                )

            if payload.corpus_c_upload_batch:
                escaped_batch = payload.corpus_c_upload_batch.replace("'", "''")
                batch_filter = f"{corpus_c_base_filter} and upload_batch eq '{escaped_batch}'"
                corpus_c_batch_total = svc._count_search_documents_total_by_filter(
                    svc.search_client,
                    filter_expr=batch_filter,
                )
                if corpus_c_batch_total <= 0:
                    return JSONResponse(
                        {
                            "error": (
                                "Compliance report is unavailable because the selected Corpus C "
                                "upload batch has no indexed documents to assess."
                            )
                        },
                        status_code=400,
                    )

            return JSONResponse(svc._generate_compliance_report_result(payload))
        except Exception as exc:
            if isinstance(exc, RuntimeError) and "schema validation failed" in str(exc).lower():
                logger.exception(
                    "Failed /api/compliance/report request due to schema validation: %s", exc
                )
                return JSONResponse(
                    {"error": "Compliance report schema validation failed."}, status_code=500
                )
            logger.exception("Failed /api/compliance/report request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/compliance/report/azure")
    def generate_azure_compliance_report(
        request: Request, payload: AzureComplianceReportRequest
    ) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            return JSONResponse(svc._generate_azure_compliance_report_result(payload))
        except Exception as exc:
            if isinstance(exc, RuntimeError) and "schema validation failed" in str(exc).lower():
                logger.exception(
                    "Failed /api/compliance/report/azure due to schema validation: %s", exc
                )
                return JSONResponse(
                    {"error": "Compliance report schema validation failed."}, status_code=500
                )
            logger.exception("Failed /api/compliance/report/azure request: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/compliance/report/start")
    def start_compliance_report(request: Request, payload: ComplianceReportRequest) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        try:
            corpus_c_base_filter = "corpus eq 'c'"
            corpus_c_indexed_total = svc._count_search_documents_total_by_filter(
                svc.search_client,
                filter_expr=corpus_c_base_filter,
            )
            if corpus_c_indexed_total <= 0:
                return JSONResponse(
                    {
                        "error": (
                            "Compliance report is unavailable because there are no Corpus C "
                            "documents to assess. Upload and index Corpus C artifacts first."
                        )
                    },
                    status_code=400,
                )

            if payload.corpus_c_upload_batch:
                escaped_batch = payload.corpus_c_upload_batch.replace("'", "''")
                batch_filter = f"{corpus_c_base_filter} and upload_batch eq '{escaped_batch}'"
                corpus_c_batch_total = svc._count_search_documents_total_by_filter(
                    svc.search_client,
                    filter_expr=batch_filter,
                )
                if corpus_c_batch_total <= 0:
                    return JSONResponse(
                        {
                            "error": (
                                "Compliance report is unavailable because the selected Corpus C "
                                "upload batch has no indexed documents to assess."
                            )
                        },
                        status_code=400,
                    )
        except Exception as exc:
            logger.exception("Failed compliance report preflight validation: %s", exc)
            return JSONResponse({"error": svc._INTERNAL_ERROR_MESSAGE}, status_code=500)

        job = _new_report_job("compliance")

        def _progress(completed: int, total: int, requirement_id: str, message: str) -> None:
            _update_report_job(
                job.job_id,
                state="running",
                message=message,
                total_controls=total,
                completed_controls=completed,
                current_requirement_id=requirement_id,
            )

        def _run() -> None:
            _update_report_job(job.job_id, state="running", message="Starting compliance report")
            try:
                result = svc._generate_compliance_report_result(payload, progress_cb=_progress)
                _update_report_job(
                    job.job_id,
                    state="completed",
                    message="Compliance report completed",
                    result=result,
                    completed_controls=max(0, int(result.get("controls_count") or 0)),
                    total_controls=max(0, int(result.get("controls_count") or 0)),
                )
            except Exception as exc:
                logger.exception("Compliance report job failed: %s", exc)
                _update_report_job(
                    job.job_id,
                    state="failed",
                    message="Compliance report failed",
                    error=svc._INTERNAL_ERROR_MESSAGE,
                )

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"job_id": job.job_id, "mode": "compliance-report-job"})

    @app.post("/api/compliance/report/azure/start")
    def start_azure_compliance_report(
        request: Request, payload: AzureComplianceReportRequest
    ) -> JSONResponse:
        if not svc._is_authorised_request(payload.auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        job = _new_report_job("azure")

        def _progress(completed: int, total: int, requirement_id: str, message: str) -> None:
            _update_report_job(
                job.job_id,
                state="running",
                message=message,
                total_controls=total,
                completed_controls=completed,
                current_requirement_id=requirement_id,
            )

        def _run() -> None:
            _update_report_job(
                job.job_id, state="running", message="Starting Azure compliance report"
            )
            try:
                result = svc._generate_azure_compliance_report_result(
                    payload, progress_cb=_progress
                )
                _update_report_job(
                    job.job_id,
                    state="completed",
                    message="Azure compliance report completed",
                    result=result,
                )
            except Exception as exc:
                logger.exception("Azure compliance report job failed: %s", exc)
                _update_report_job(
                    job.job_id,
                    state="failed",
                    message="Azure compliance report failed",
                    error=svc._INTERNAL_ERROR_MESSAGE,
                )

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"job_id": job.job_id, "mode": "azure-compliance-report-job"})

    @app.get("/api/compliance/report/jobs/{job_id}")
    def get_compliance_report_job(
        job_id: str, request: Request, auth_token: str = ""
    ) -> JSONResponse:
        if not svc._is_authorised_request(auth_token, request):
            return JSONResponse({"error": svc._unauthorised_message(request)}, status_code=401)

        job = _get_report_job(job_id)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        return JSONResponse(
            {
                "job_id": job.job_id,
                "kind": job.kind,
                "state": job.state,
                "message": job.message,
                "total_controls": job.total_controls,
                "completed_controls": job.completed_controls,
                "current_requirement_id": job.current_requirement_id,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "error": job.error,
                "has_result": job.result is not None,
                "result": job.result if job.state == "completed" else None,
            }
        )
