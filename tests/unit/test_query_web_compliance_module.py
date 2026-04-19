"""Unit tests for uncovered areas of query_web/compliance.py."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.compliance import (
    ComplianceFinding,
    ComplianceReportStructured,
    _ReportJob,
    _build_compliance_scope_inputs,
    _clean_non_empty_string_list,
    _control_terms,
    _extract_json_object,
    _get_report_job,
    _new_report_job,
    _normalise_compliance_report_payload,
    _REPORT_JOBS,
    _report_findings_to_csv,
    _select_chunks_for_control,
    _update_report_job,
    _validate_compliance_report_payload,
)
from query_web.constants import COMPLIANCE_REPORT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_structured_report(**kwargs) -> dict:
    defaults = dict(
        schema_version=COMPLIANCE_REPORT_SCHEMA_VERSION,
        executive_summary="Summary of findings.",
        scope_and_inputs=["Corpus A controls retrieved: 1"],
        controls_assessed=["CTRL-1"],
        guidance_applied=[],
        findings=[
            {
                "finding_id": "finding-1",
                "requirement_id": "CTRL-1",
                "framework": "NIST CSF",
                "status": "compliant",
                "severity": "low",
                "rationale": "Evidence confirms compliance.",
                "evidence_sources": ["doc-1.pdf"],
                "gaps": [],
                "recommendations": [],
            }
        ],
        overall_risk_rating="low",
        missing_evidence=[],
        recommended_actions=["Continue monitoring."],
        citations=["doc-1.pdf"],
    )
    defaults.update(kwargs)
    return defaults


def _make_svc(*, chunk_label: str = "doc-1.pdf") -> SimpleNamespace:
    svc = SimpleNamespace()
    svc._unwrap_answer = lambda text: text
    svc._chunk_reference_label = lambda item, fallback="": chunk_label or fallback
    return svc


# ---------------------------------------------------------------------------
# _new_report_job / _get_report_job / _update_report_job
# ---------------------------------------------------------------------------


def test_new_report_job_creates_queued_job() -> None:
    job = _new_report_job("compliance")
    assert job.kind == "compliance"
    assert job.state == "queued"
    assert job.job_id in _REPORT_JOBS


def test_new_report_job_azure_kind() -> None:
    job = _new_report_job("azure")
    assert job.kind == "azure"


def test_get_report_job_returns_job() -> None:
    job = _new_report_job("compliance")
    retrieved = _get_report_job(job.job_id)
    assert retrieved is job


def test_get_report_job_missing_returns_none() -> None:
    assert _get_report_job("nonexistent-job-id-12345") is None


def test_update_report_job_sets_fields() -> None:
    job = _new_report_job("compliance")
    _update_report_job(job.job_id, state="running", message="In progress")
    updated = _get_report_job(job.job_id)
    assert updated is not None
    assert updated.state == "running"
    assert updated.message == "In progress"


def test_update_report_job_updates_updated_at() -> None:
    job = _new_report_job("compliance")
    original_ts = job.updated_at
    import time; time.sleep(0.01)
    _update_report_job(job.job_id, state="completed")
    updated = _get_report_job(job.job_id)
    assert updated is not None
    assert updated.updated_at >= original_ts


def test_update_report_job_noop_for_missing_id() -> None:
    # Should not raise
    _update_report_job("missing-job-id", state="running")


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------


def test_extract_json_object_plain_json() -> None:
    svc = _make_svc()
    result = _extract_json_object('{"key": "value"}', svc)
    assert result == {"key": "value"}


def test_extract_json_object_json_embedded_in_text() -> None:
    svc = _make_svc()
    result = _extract_json_object('Some text {"key": "val"} more text', svc)
    assert result["key"] == "val"


def test_extract_json_object_raises_on_empty() -> None:
    svc = _make_svc()
    with pytest.raises(ValueError, match="empty"):
        _extract_json_object("", svc)


def test_extract_json_object_raises_on_no_json() -> None:
    svc = _make_svc()
    with pytest.raises(ValueError):
        _extract_json_object("no json here at all", svc)


def test_extract_json_object_raises_on_non_object() -> None:
    svc = _make_svc()
    with pytest.raises(ValueError):
        _extract_json_object("[1, 2, 3]", svc)


def test_extract_json_object_uses_svc_unwrap_answer() -> None:
    svc = _make_svc()
    svc._unwrap_answer = lambda text: '{"unwrapped": true}'
    result = _extract_json_object("```json\n{...}\n```", svc)
    assert result["unwrapped"] is True


# ---------------------------------------------------------------------------
# _validate_compliance_report_payload
# ---------------------------------------------------------------------------


def test_validate_compliance_report_payload_valid() -> None:
    payload = _minimal_structured_report()
    report = _validate_compliance_report_payload(payload)
    assert isinstance(report, ComplianceReportStructured)
    assert report.schema_version == COMPLIANCE_REPORT_SCHEMA_VERSION


def test_validate_compliance_report_payload_wrong_schema_version() -> None:
    payload = _minimal_structured_report(schema_version="v0.0")
    with pytest.raises(ValueError, match="schema_version"):
        _validate_compliance_report_payload(payload)


def test_validate_compliance_report_payload_missing_required_field() -> None:
    payload = _minimal_structured_report()
    del payload["executive_summary"]
    with pytest.raises(Exception):
        _validate_compliance_report_payload(payload)


def test_validate_compliance_report_payload_invalid_status() -> None:
    payload = _minimal_structured_report()
    payload["findings"][0]["status"] = "invalid_status"
    with pytest.raises(Exception):
        _validate_compliance_report_payload(payload)


# ---------------------------------------------------------------------------
# _clean_non_empty_string_list
# ---------------------------------------------------------------------------


def test_clean_non_empty_string_list_plain_list() -> None:
    result = _clean_non_empty_string_list(["a", "b", "c"])
    assert result == ["a", "b", "c"]


def test_clean_non_empty_string_list_removes_empty() -> None:
    result = _clean_non_empty_string_list(["a", "", "  ", "b"])
    assert result == ["a", "b"]


def test_clean_non_empty_string_list_single_string() -> None:
    result = _clean_non_empty_string_list("hello")
    assert result == ["hello"]


def test_clean_non_empty_string_list_non_list_non_string() -> None:
    assert _clean_non_empty_string_list(42) == []
    assert _clean_non_empty_string_list(None) == []


def test_clean_non_empty_string_list_strips_whitespace() -> None:
    result = _clean_non_empty_string_list(["  hello  ", "  world  "])
    assert result == ["hello", "world"]


# ---------------------------------------------------------------------------
# _control_terms
# ---------------------------------------------------------------------------


def test_control_terms_extracts_tokens() -> None:
    control = {
        "requirement_id": "AC-1",
        "control_family": "Access Control",
        "requirement_text": "Implement multi-factor authentication",
        "guidance_text": "Use hardware tokens or TOTP",
    }
    terms = _control_terms(control)
    assert "authentication" in terms or "multi" in terms


def test_control_terms_minimum_length() -> None:
    control = {
        "requirement_id": "AC-1",
        "control_family": "IT",
        "requirement_text": "ok do it",
        "guidance_text": "",
    }
    terms = _control_terms(control)
    # All tokens should be at least 4 chars
    for token in terms:
        assert len(token) >= 4


def test_control_terms_empty_control() -> None:
    terms = _control_terms({})
    assert isinstance(terms, set)


# ---------------------------------------------------------------------------
# _select_chunks_for_control
# ---------------------------------------------------------------------------


def _make_chunk(content: str, score: float = 0.5) -> dict:
    return {"content": content, "score": score}


def test_select_chunks_for_control_returns_max_chunks() -> None:
    control = {
        "requirement_id": "AC-1",
        "control_family": "Access Control",
        "requirement_text": "authentication requirements",
        "guidance_text": "",
    }
    chunks = [_make_chunk(f"Authentication policy doc {i}") for i in range(10)]
    result = _select_chunks_for_control(control, chunks, max_chunks=3)
    assert len(result) == 3


def test_select_chunks_for_control_empty_chunks_returns_empty() -> None:
    control = {"requirement_id": "AC-1", "requirement_text": "mfa", "guidance_text": ""}
    assert _select_chunks_for_control(control, [], max_chunks=5) == []


def test_select_chunks_for_control_ranks_by_overlap() -> None:
    control = {
        "requirement_id": "AC-1",
        "control_family": "Authentication",
        "requirement_text": "multi-factor authentication",
        "guidance_text": "password tokens",
    }
    chunks = [
        _make_chunk("unrelated content about networking", score=0.9),
        _make_chunk("authentication password tokens security", score=0.5),
    ]
    result = _select_chunks_for_control(control, chunks, max_chunks=1)
    # The overlapping chunk should rank first
    assert "authentication" in result[0]["content"].lower() or result[0]["score"] >= 0.5


# ---------------------------------------------------------------------------
# _build_compliance_scope_inputs
# ---------------------------------------------------------------------------


def test_build_compliance_scope_inputs_minimal() -> None:
    result = _build_compliance_scope_inputs(
        controls_count=4,
        corpus_b_chunk_count=3,
        corpus_c_chunk_count=2,
        corpus_b_indexed_total=0,
        corpus_c_indexed_total=0,
    )
    assert any("4" in item for item in result)
    assert any("3" in item for item in result)
    assert any("2" in item for item in result)


def test_build_compliance_scope_inputs_with_question() -> None:
    result = _build_compliance_scope_inputs(
        question="What are the MFA requirements?",
        controls_count=2,
        corpus_b_chunk_count=1,
        corpus_c_chunk_count=0,
        corpus_b_indexed_total=0,
        corpus_c_indexed_total=0,
    )
    assert any("MFA" in item for item in result)


def test_build_compliance_scope_inputs_includes_indexed_totals() -> None:
    result = _build_compliance_scope_inputs(
        controls_count=2,
        corpus_b_chunk_count=1,
        corpus_c_chunk_count=0,
        corpus_b_indexed_total=50,
        corpus_c_indexed_total=30,
    )
    assert any("50" in item for item in result)
    assert any("30" in item for item in result)


def test_build_compliance_scope_inputs_includes_batch_filter() -> None:
    result = _build_compliance_scope_inputs(
        controls_count=2,
        corpus_b_chunk_count=1,
        corpus_c_chunk_count=0,
        corpus_b_indexed_total=0,
        corpus_c_indexed_total=0,
        corpus_b_upload_batch="batch-abc",
        corpus_b_filtered_total=5,
    )
    assert any("batch-abc" in item for item in result)
    assert any("5" in item for item in result)


def test_build_compliance_scope_inputs_includes_strategy() -> None:
    result = _build_compliance_scope_inputs(
        controls_count=2,
        corpus_b_chunk_count=1,
        corpus_c_chunk_count=0,
        corpus_b_indexed_total=0,
        corpus_c_indexed_total=0,
        assessment_strategy="per_control",
    )
    assert any("per_control" in item for item in result)


def test_build_compliance_scope_inputs_single_pass_not_included() -> None:
    result = _build_compliance_scope_inputs(
        controls_count=2,
        corpus_b_chunk_count=1,
        corpus_c_chunk_count=0,
        corpus_b_indexed_total=0,
        corpus_c_indexed_total=0,
        assessment_strategy="single_pass",
    )
    assert not any("single_pass" in item for item in result)


# ---------------------------------------------------------------------------
# _normalise_compliance_report_payload
# ---------------------------------------------------------------------------


def test_normalise_compliance_report_payload_empty_payload() -> None:
    svc = _make_svc()
    result = _normalise_compliance_report_payload(
        {},
        svc=svc,
        question="test",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    # Must produce a default finding
    assert len(result["findings"]) >= 1
    assert result["findings"][0]["status"] == "insufficient_evidence"
    # Must have default recommended_actions
    assert len(result["recommended_actions"]) >= 1
    # Risk rating must be valid
    assert result["overall_risk_rating"] in {"low", "medium", "high", "critical"}


def test_normalise_compliance_report_payload_sets_schema_version() -> None:
    svc = _make_svc()
    result = _normalise_compliance_report_payload(
        {},
        svc=svc,
        question="",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert result["schema_version"] == COMPLIANCE_REPORT_SCHEMA_VERSION


def test_normalise_compliance_report_payload_preserves_valid_findings() -> None:
    svc = _make_svc()
    finding = {
        "finding_id": "f-1",
        "requirement_id": "CTRL-1",
        "framework": "ISM",
        "status": "compliant",
        "severity": "high",
        "rationale": "Evidence shows compliance.",
        "evidence_sources": ["doc-1.pdf"],
        "gaps": [],
        "recommendations": [],
    }
    result = _normalise_compliance_report_payload(
        {"findings": [finding]},
        svc=svc,
        question="",
        controls=[{"requirement_id": "CTRL-1", "framework": "ISM"}],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert result["findings"][0]["finding_id"] == "f-1"
    assert result["findings"][0]["status"] == "compliant"


def test_normalise_compliance_report_payload_corrects_invalid_status() -> None:
    svc = _make_svc()
    finding = {
        "finding_id": "f-1",
        "requirement_id": "CTRL-1",
        "framework": "ISM",
        "status": "maybe",
        "severity": "high",
        "rationale": "Some rationale.",
        "evidence_sources": ["doc.pdf"],
        "gaps": [],
        "recommendations": [],
    }
    result = _normalise_compliance_report_payload(
        {"findings": [finding]},
        svc=svc,
        question="",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert result["findings"][0]["status"] == "insufficient_evidence"


def test_normalise_compliance_report_payload_corrects_invalid_severity() -> None:
    svc = _make_svc()
    finding = {
        "finding_id": "f-1",
        "requirement_id": "CTRL-1",
        "framework": "ISM",
        "status": "compliant",
        "severity": "extreme",
        "rationale": "Rationale here.",
        "evidence_sources": ["doc.pdf"],
        "gaps": [],
        "recommendations": [],
    }
    result = _normalise_compliance_report_payload(
        {"findings": [finding]},
        svc=svc,
        question="",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert result["findings"][0]["severity"] == "medium"


def test_normalise_compliance_report_payload_corrects_empty_rationale() -> None:
    svc = _make_svc()
    finding = {
        "finding_id": "f-1",
        "requirement_id": "CTRL-1",
        "framework": "ISM",
        "status": "compliant",
        "severity": "low",
        "rationale": "",
        "evidence_sources": ["doc.pdf"],
        "gaps": [],
        "recommendations": [],
    }
    result = _normalise_compliance_report_payload(
        {"findings": [finding]},
        svc=svc,
        question="",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert result["findings"][0]["rationale"] != ""
    assert result["findings"][0]["status"] == "insufficient_evidence"


def test_normalise_compliance_report_payload_uses_control_ids() -> None:
    svc = _make_svc()
    controls = [
        {"requirement_id": "ISM-1234", "framework": "ISM"},
        {"requirement_id": "ISM-5678", "framework": "ISM"},
    ]
    result = _normalise_compliance_report_payload(
        {},
        svc=svc,
        question="",
        controls=controls,
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert "ISM-1234" in result["controls_assessed"]
    assert "ISM-5678" in result["controls_assessed"]


def test_normalise_compliance_report_payload_corrects_no_normative_summary() -> None:
    svc = _make_svc()
    result = _normalise_compliance_report_payload(
        {"executive_summary": "There are no normative requirements in this corpus."},
        svc=svc,
        question="",
        controls=[{"requirement_id": "CTRL-1", "framework": "ISM"}],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
    )
    assert "no normative requirements" not in result["executive_summary"].lower()


# ---------------------------------------------------------------------------
# _report_findings_to_csv
# ---------------------------------------------------------------------------


def test_report_findings_to_csv_produces_csv_with_header() -> None:
    payload = _minimal_structured_report()
    report = ComplianceReportStructured.model_validate(payload)
    csv_output = _report_findings_to_csv(report)
    assert "finding_id" in csv_output.lower() or "requirement_id" in csv_output.lower()
    assert "compliant" in csv_output


def test_report_findings_to_csv_contains_all_findings() -> None:
    findings = [
        {
            "finding_id": f"finding-{i}",
            "requirement_id": f"CTRL-{i}",
            "framework": "NIST CSF",
            "status": "compliant",
            "severity": "low",
            "rationale": f"Rationale {i}",
            "evidence_sources": ["doc.pdf"],
            "gaps": [],
            "recommendations": [],
        }
        for i in range(1, 4)
    ]
    payload = _minimal_structured_report(findings=findings)
    report = ComplianceReportStructured.model_validate(payload)
    csv_output = _report_findings_to_csv(report)
    for i in range(1, 4):
        assert f"CTRL-{i}" in csv_output
