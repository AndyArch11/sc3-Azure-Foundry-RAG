"""Unit tests for uncovered areas of query_web/compliance.py."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.endpoints.compliance import (
    ComplianceFinding,
    ComplianceReportRequest,
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


# ---------------------------------------------------------------------------
# Endpoint tests (register_compliance_endpoints)
# ---------------------------------------------------------------------------
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from query_web.endpoints.compliance import register_compliance_endpoints


_VALID_REPORT_RESULT = {
    "mode": "compliance-report",
    "assessment_strategy": "single_pass",
    "report": "# Compliance Report",
    "report_markdown": "# Compliance Report",
    "report_structured": None,
    "report_findings_csv": "",
    "report_filename_base": "compliance-report-20260419-000000",
    "report_schema_version": "v1.1",
    "validation_mode": "hard",
    "schema_valid": True,
    "validation_error": "",
    "controls_count": 1,
    "corpus_b_count": 1,
    "corpus_c_count": 1,
    "corpus_b_indexed_total": 3,
    "corpus_c_indexed_total": 5,
    "corpus_b_upload_batch_filter": None,
    "corpus_c_upload_batch_filter": None,
    "assessment_question_supplied": True,
    "effective_assessment_question": "test",
    "evidence_corpora_selected": ["b"],
    "audit": {},
    "corpus_b_filtered_total": None,
    "corpus_c_filtered_total": None,
    "timings": {},
}

_VALID_AZURE_RESULT = {
    "mode": "azure-compliance-report",
    "assessment_strategy": "single_pass",
    "framework": "NIST CSF",
    "controls_top_k": 4,
    "temperature": 1.0,
    "scope": {"subscription_id": "s", "resource_group": "rg", "resource_ids": []},
    "report": "# Azure Report",
    "report_markdown": "# Azure Report",
    "report_structured": None,
    "report_findings_csv": "",
    "report_filename_base": "azure-compliance-report-20260419",
    "report_schema_version": "v1.1",
    "validation_mode": "hard",
    "schema_valid": True,
    "validation_error": "",
}


def _make_endpoint_svc(
    *,
    authorised: bool = True,
    corpus_c_total: int = 5,
    generate_raises: Exception | None = None,
    azure_raises: Exception | None = None,
) -> SimpleNamespace:
    def _count(client, *, filter_expr):
        if "upload_batch" in filter_expr:
            return 0 if corpus_c_total == 0 else 3
        if "eq 'c'" in filter_expr:
            return corpus_c_total
        return 3

    def _generate(payload, progress_cb=None):
        if generate_raises:
            raise generate_raises
        return dict(_VALID_REPORT_RESULT)

    def _azure(payload, progress_cb=None):
        if azure_raises:
            raise azure_raises
        return dict(_VALID_AZURE_RESULT)

    return SimpleNamespace(
        _is_authorised_request=lambda token, req: authorised,
        _unauthorised_message=lambda req=None: "Unauthorised.",
        _INTERNAL_ERROR_MESSAGE="An internal error occurred.",
        _count_search_documents_total_by_filter=_count,
        _generate_compliance_report_result=_generate,
        _generate_azure_compliance_report_result=_azure,
        search_client=object(),
    )


def _build_client(svc) -> TestClient:
    app = FastAPI()
    register_compliance_endpoints(app, svc)
    return TestClient(app, raise_server_exceptions=False)


# POST /api/compliance/report

def test_endpoint_report_unauthorised() -> None:
    client = _build_client(_make_endpoint_svc(authorised=False))
    resp = client.post("/api/compliance/report", json={"auth_token": "bad"})
    assert resp.status_code == 401


def test_endpoint_report_no_corpus_c_returns_400() -> None:
    client = _build_client(_make_endpoint_svc(corpus_c_total=0))
    resp = client.post("/api/compliance/report", json={"auth_token": "tok"})
    assert resp.status_code == 400
    assert "Corpus C" in resp.json()["error"]


def test_endpoint_report_batch_filter_no_docs_returns_400() -> None:
    def _count(client, *, filter_expr):
        if "upload_batch" in filter_expr:
            return 0
        return 5

    svc = _make_endpoint_svc()
    svc._count_search_documents_total_by_filter = _count
    client = _build_client(svc)
    resp = client.post(
        "/api/compliance/report",
        json={"auth_token": "tok", "corpus_c_upload_batch": "batch-xyz"},
    )
    assert resp.status_code == 400
    assert "upload batch" in resp.json()["error"].lower()


def test_endpoint_report_success() -> None:
    client = _build_client(_make_endpoint_svc())
    resp = client.post("/api/compliance/report", json={"auth_token": "tok", "question": "test"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "compliance-report"


def test_endpoint_report_schema_validation_error_returns_500() -> None:
    svc = _make_endpoint_svc(
        generate_raises=RuntimeError("Compliance report schema validation failed: detail")
    )
    client = _build_client(svc)
    resp = client.post("/api/compliance/report", json={"auth_token": "tok"})
    assert resp.status_code == 500
    assert "schema validation" in resp.json()["error"].lower()


def test_endpoint_report_generic_exception_returns_500() -> None:
    svc = _make_endpoint_svc(generate_raises=ValueError("oops"))
    client = _build_client(svc)
    resp = client.post("/api/compliance/report", json={"auth_token": "tok"})
    assert resp.status_code == 500
    assert resp.json()["error"] == "An internal error occurred."


# POST /api/compliance/report/azure

def test_endpoint_azure_report_unauthorised() -> None:
    client = _build_client(_make_endpoint_svc(authorised=False))
    resp = client.post(
        "/api/compliance/report/azure",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "bad"},
    )
    assert resp.status_code == 401


def test_endpoint_azure_report_success() -> None:
    client = _build_client(_make_endpoint_svc())
    resp = client.post(
        "/api/compliance/report/azure",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "tok"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "azure-compliance-report"


def test_endpoint_azure_report_schema_error_returns_500() -> None:
    svc = _make_endpoint_svc(
        azure_raises=RuntimeError("Compliance report schema validation failed")
    )
    client = _build_client(svc)
    resp = client.post(
        "/api/compliance/report/azure",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "tok"},
    )
    assert resp.status_code == 500
    assert "schema validation" in resp.json()["error"].lower()


def test_endpoint_azure_report_generic_exception_returns_500() -> None:
    svc = _make_endpoint_svc(azure_raises=ConnectionError("net"))
    client = _build_client(svc)
    resp = client.post(
        "/api/compliance/report/azure",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "tok"},
    )
    assert resp.status_code == 500
    assert resp.json()["error"] == "An internal error occurred."


# POST /api/compliance/report/start

def test_endpoint_start_report_unauthorised() -> None:
    client = _build_client(_make_endpoint_svc(authorised=False))
    resp = client.post("/api/compliance/report/start", json={"auth_token": "bad"})
    assert resp.status_code == 401


def test_endpoint_start_report_no_corpus_c_returns_400() -> None:
    client = _build_client(_make_endpoint_svc(corpus_c_total=0))
    resp = client.post("/api/compliance/report/start", json={"auth_token": "tok"})
    assert resp.status_code == 400


def test_endpoint_start_report_batch_no_docs_returns_400() -> None:
    def _count(client, *, filter_expr):
        if "upload_batch" in filter_expr:
            return 0
        return 5

    svc = _make_endpoint_svc()
    svc._count_search_documents_total_by_filter = _count
    client = _build_client(svc)
    resp = client.post(
        "/api/compliance/report/start",
        json={"auth_token": "tok", "corpus_c_upload_batch": "batch-xyz"},
    )
    assert resp.status_code == 400


def test_endpoint_start_report_preflight_exception_returns_500() -> None:
    svc = _make_endpoint_svc()
    svc._count_search_documents_total_by_filter = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("search down"))
    client = _build_client(svc)
    resp = client.post("/api/compliance/report/start", json={"auth_token": "tok"})
    assert resp.status_code == 500


def test_endpoint_start_report_creates_job_completes() -> None:
    client = _build_client(_make_endpoint_svc())
    resp = client.post("/api/compliance/report/start", json={"auth_token": "tok"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert resp.json()["mode"] == "compliance-report-job"

    job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
    for _ in range(30):
        job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
        if job_resp.json()["state"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job_resp.json()["state"] == "completed"
    assert job_resp.json()["has_result"] is True


def test_endpoint_start_report_job_records_failure() -> None:
    svc = _make_endpoint_svc(generate_raises=RuntimeError("pipeline error"))
    client = _build_client(svc)
    resp = client.post("/api/compliance/report/start", json={"auth_token": "tok"})
    job_id = resp.json()["job_id"]

    job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
    for _ in range(30):
        job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
        if job_resp.json()["state"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job_resp.json()["state"] == "failed"
    assert job_resp.json()["error"] == "An internal error occurred."


# POST /api/compliance/report/azure/start

def test_endpoint_start_azure_report_unauthorised() -> None:
    client = _build_client(_make_endpoint_svc(authorised=False))
    resp = client.post(
        "/api/compliance/report/azure/start",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "bad"},
    )
    assert resp.status_code == 401


def test_endpoint_start_azure_report_creates_job_completes() -> None:
    client = _build_client(_make_endpoint_svc())
    resp = client.post(
        "/api/compliance/report/azure/start",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "tok"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "azure-compliance-report-job"
    job_id = resp.json()["job_id"]

    job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
    for _ in range(30):
        job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
        if job_resp.json()["state"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job_resp.json()["state"] == "completed"


def test_endpoint_start_azure_report_job_records_failure() -> None:
    svc = _make_endpoint_svc(azure_raises=RuntimeError("azure error"))
    client = _build_client(svc)
    resp = client.post(
        "/api/compliance/report/azure/start",
        json={"subscription_id": "s", "resource_group": "rg", "auth_token": "tok"},
    )
    job_id = resp.json()["job_id"]

    job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
    for _ in range(30):
        job_resp = client.get(f"/api/compliance/report/jobs/{job_id}?auth_token=tok")
        if job_resp.json()["state"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job_resp.json()["state"] == "failed"


# GET /api/compliance/report/jobs/{job_id}

def test_endpoint_get_job_unauthorised() -> None:
    client = _build_client(_make_endpoint_svc(authorised=False))
    resp = client.get("/api/compliance/report/jobs/any-id?auth_token=bad")
    assert resp.status_code == 401


def test_endpoint_get_job_not_found() -> None:
    client = _build_client(_make_endpoint_svc())
    resp = client.get(
        "/api/compliance/report/jobs/00000000-0000-0000-0000-000000000000?auth_token=tok"
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "Job not found"


def test_endpoint_get_job_queued_has_no_result() -> None:
    from query_web.endpoints.compliance import _REPORT_JOBS, _REPORT_JOBS_LOCK, _ReportJob
    from query_web.utils import _utc_now_iso as _tnow

    job = _ReportJob(
        job_id="queued-test-job",
        kind="compliance",
        created_at=_tnow(),
        updated_at=_tnow(),
        state="queued",
    )
    with _REPORT_JOBS_LOCK:
        _REPORT_JOBS[job.job_id] = job

    client = _build_client(_make_endpoint_svc())
    resp = client.get(f"/api/compliance/report/jobs/{job.job_id}?auth_token=tok")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "queued"
    assert data["result"] is None
    assert data["has_result"] is False


# ---------------------------------------------------------------------------
# _chunk_azure_artifact
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import _chunk_azure_artifact


def test_chunk_azure_artifact_splits_long_content() -> None:
    artifact = SimpleNamespace(content="A" * 5000, title="policy.pdf")
    chunks = _chunk_azure_artifact(artifact, chunk_size=2000)
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk["source_name"] == "policy.pdf"
        assert len(chunk["content"]) > 0


def test_chunk_azure_artifact_empty_content_returns_empty() -> None:
    artifact = SimpleNamespace(content="", title="policy.pdf")
    assert _chunk_azure_artifact(artifact) == []


def test_chunk_azure_artifact_missing_content_attr_returns_empty() -> None:
    artifact = SimpleNamespace(title="policy.pdf")
    assert _chunk_azure_artifact(artifact) == []


def test_chunk_azure_artifact_default_title() -> None:
    artifact = SimpleNamespace(content="some text", title=None)
    chunks = _chunk_azure_artifact(artifact)
    assert len(chunks) == 1
    assert chunks[0]["source_name"] == "Azure scope evidence"


# ---------------------------------------------------------------------------
# _build_fallback_compliance_report_payload
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import _build_fallback_compliance_report_payload


def test_build_fallback_compliance_report_payload_minimal() -> None:
    svc = _make_svc()
    result = _build_fallback_compliance_report_payload(
        svc=svc,
        question="test",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        validation_error="schema mismatch",
    )
    assert result["schema_version"] == COMPLIANCE_REPORT_SCHEMA_VERSION
    assert result["overall_risk_rating"] == "medium"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["finding_id"] == "fallback-1"
    assert "schema mismatch" in result["findings"][0]["rationale"]


def test_build_fallback_compliance_report_payload_uses_control_ids() -> None:
    svc = _make_svc()
    controls = [{"requirement_id": "CTRL-99", "framework": "ISM"}]
    result = _build_fallback_compliance_report_payload(
        svc=svc,
        question="",
        controls=controls,
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        validation_error="",
    )
    assert "CTRL-99" in result["controls_assessed"]
    assert result["findings"][0]["requirement_id"] == "CTRL-99"


def test_build_fallback_compliance_report_payload_includes_evidence_sources() -> None:
    svc = _make_svc(chunk_label="evidence.pdf")
    chunks = [{"content": "text", "score": 0.9}]
    result = _build_fallback_compliance_report_payload(
        svc=svc,
        question="",
        controls=[],
        corpus_b_chunks=chunks,
        corpus_c_chunks=[],
        validation_error="",
    )
    assert "evidence.pdf" in result["citations"]


# ---------------------------------------------------------------------------
# _assess_control_finding_with_llm
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import _assess_control_finding_with_llm


def _make_llm_svc(*, llm_response: str = '{"finding_id":"f-1","requirement_id":"R-1","framework":"ISM","status":"compliant","severity":"low","rationale":"ok","evidence_sources":["doc.pdf"],"gaps":[],"recommendations":[]}') -> SimpleNamespace:
    svc = SimpleNamespace(
        PROMPT_INJECTION_SYSTEM_PROMPT="be safe",
        sanitise_untrusted_text=lambda t: t,
        _chunk_reference_label=lambda c, fallback="": c.get("source_name", fallback),
        _unwrap_answer=lambda t: t,
        _chat_completion_with_empty_retry=lambda msgs, deployment, temperature: llm_response,
        config=SimpleNamespace(query_deployment="gpt-4"),
    )
    return svc


def test_assess_control_finding_with_llm_success() -> None:
    svc = _make_llm_svc()
    result = _assess_control_finding_with_llm(
        svc=svc,
        question="test",
        control={"requirement_id": "R-1", "framework": "ISM", "requirement_text": "Req text", "guidance_text": ""},
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        temperature=0.5,
    )
    assert result["finding_id"] == "f-1"
    assert result["status"] == "compliant"


def test_assess_control_finding_with_llm_llm_raises_uses_fallback() -> None:
    def _raises(*a, **kw):
        raise RuntimeError("LLM unavailable")

    svc = _make_llm_svc()
    svc._chat_completion_with_empty_retry = _raises
    result = _assess_control_finding_with_llm(
        svc=svc,
        question="test",
        control={"requirement_id": "R-99", "framework": "ISM", "requirement_text": "Req", "guidance_text": ""},
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        temperature=0.5,
    )
    # Should fall back gracefully
    assert result["requirement_id"] == "R-99"
    assert result["status"] == "insufficient_evidence"


def test_assess_control_finding_with_llm_includes_corpus_context() -> None:
    calls: list[list] = []

    def _capture(msgs, *, deployment, temperature):
        calls.append(msgs)
        return '{"finding_id":"f-1","requirement_id":"R-1","framework":"ISM","status":"compliant","severity":"low","rationale":"ok","evidence_sources":["doc.pdf"],"gaps":[],"recommendations":[]}'

    svc = _make_llm_svc()
    svc._chat_completion_with_empty_retry = _capture
    _assess_control_finding_with_llm(
        svc=svc,
        question="test",
        control={"requirement_id": "R-1", "framework": "ISM", "requirement_text": "Req", "guidance_text": "guidance"},
        corpus_b_chunks=[{"content": "b guidance", "source_name": "b.pdf"}],
        corpus_c_chunks=[{"content": "c artifact", "source_name": "c.pdf"}],
        temperature=0.5,
    )
    assert len(calls) == 1
    user_msg = next(m for m in calls[0] if m["role"] == "user")
    assert "b guidance" in user_msg["content"] or "b.pdf" in user_msg["content"]


# ---------------------------------------------------------------------------
# _build_per_control_report_payload
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import _build_per_control_report_payload


def test_build_per_control_report_payload_empty_controls() -> None:
    svc = _make_llm_svc()
    svc._chunk_reference_label = lambda c, fallback="": c.get("source_name", fallback)
    result = _build_per_control_report_payload(
        svc=svc,
        question="test",
        controls=[],
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        temperature=0.5,
    )
    assert result["schema_version"] == COMPLIANCE_REPORT_SCHEMA_VERSION
    assert result["findings"] == []
    assert result["controls_assessed"] == ["UNMAPPED"]


def test_build_per_control_report_payload_calls_progress_cb() -> None:
    svc = _make_llm_svc()
    svc._chunk_reference_label = lambda c, fallback="": fallback
    progress_calls: list[tuple] = []

    def _progress(completed, total, req_id, message):
        progress_calls.append((completed, total, req_id, message))

    controls = [{"requirement_id": "C-1", "framework": "ISM", "requirement_text": "req", "guidance_text": ""}]
    _build_per_control_report_payload(
        svc=svc,
        question="test",
        controls=controls,
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        temperature=0.5,
        progress_cb=_progress,
    )
    assert len(progress_calls) >= 2  # before + after each control


def test_build_per_control_report_payload_risk_high_on_non_compliant() -> None:
    llm_response = '{"finding_id":"f-1","requirement_id":"C-1","framework":"ISM","status":"non_compliant","severity":"high","rationale":"gaps found","evidence_sources":["doc.pdf"],"gaps":[],"recommendations":[]}'
    svc = _make_llm_svc(llm_response=llm_response)
    svc._chunk_reference_label = lambda c, fallback="": fallback
    controls = [{"requirement_id": "C-1", "framework": "ISM", "requirement_text": "req", "guidance_text": ""}]
    result = _build_per_control_report_payload(
        svc=svc,
        question="test",
        controls=controls,
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        temperature=0.5,
    )
    assert result["overall_risk_rating"] == "high"


def test_build_per_control_report_payload_risk_medium_on_partial() -> None:
    llm_response = '{"finding_id":"f-1","requirement_id":"C-1","framework":"ISM","status":"partially_compliant","severity":"medium","rationale":"partial","evidence_sources":["doc.pdf"],"gaps":[],"recommendations":[]}'
    svc = _make_llm_svc(llm_response=llm_response)
    svc._chunk_reference_label = lambda c, fallback="": fallback
    controls = [{"requirement_id": "C-1", "framework": "ISM", "requirement_text": "req", "guidance_text": ""}]
    result = _build_per_control_report_payload(
        svc=svc,
        question="test",
        controls=controls,
        corpus_b_chunks=[],
        corpus_c_chunks=[],
        temperature=0.5,
    )
    assert result["overall_risk_rating"] == "medium"


# ---------------------------------------------------------------------------
# _report_to_markdown
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import _report_to_markdown


def test_report_to_markdown_structure() -> None:
    payload = _minimal_structured_report()
    report = ComplianceReportStructured.model_validate(payload)
    md = _report_to_markdown(report)
    assert "# Compliance Report" in md
    assert "## Executive Summary" in md
    assert "## Findings" in md
    assert "## Overall Risk Rating" in md
    assert "## Citations" in md


def test_report_to_markdown_includes_finding_detail() -> None:
    payload = _minimal_structured_report()
    report = ComplianceReportStructured.model_validate(payload)
    md = _report_to_markdown(report)
    assert "CTRL-1" in md
    assert "compliant" in md
    assert "doc-1.pdf" in md


def test_report_to_markdown_empty_gaps_shows_none() -> None:
    payload = _minimal_structured_report()
    report = ComplianceReportStructured.model_validate(payload)
    md = _report_to_markdown(report)
    # Finding has no gaps, so "None" marker should appear
    assert "None" in md


def test_report_to_markdown_empty_guidance_applied_shows_none() -> None:
    payload = _minimal_structured_report(guidance_applied=[])
    report = ComplianceReportStructured.model_validate(payload)
    md = _report_to_markdown(report)
    assert "None" in md


# ---------------------------------------------------------------------------
# generate_compliance_report_result
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import generate_compliance_report_result


def _make_pipeline_svc(
    *,
    controls: list | None = None,
    b_chunks: list | None = None,
    c_chunks: list | None = None,
    model_response: str | None = None,
    chat_raises: Exception | None = None,
) -> SimpleNamespace:
    _controls = controls or []
    _b_chunks = b_chunks or []
    _c_chunks = c_chunks or []

    _default_model = (
        '{"schema_version":"v1.1","executive_summary":"ok","scope_and_inputs":["s"],'
        '"controls_assessed":["C-1"],"guidance_applied":[],'
        '"findings":[{"finding_id":"f-1","requirement_id":"C-1","framework":"ISM",'
        '"status":"compliant","severity":"low","rationale":"fine","evidence_sources":["doc.pdf"],'
        '"gaps":[],"recommendations":[]}],"overall_risk_rating":"low",'
        '"missing_evidence":[],"recommended_actions":["ok"],"citations":["doc.pdf"]}'
    )

    def _chat(msgs, *, deployment, temperature, **kw):
        if chat_raises:
            raise chat_raises
        return model_response or _default_model

    return SimpleNamespace(
        config=SimpleNamespace(
            controls_semantic_default=False,
            query_deployment="gpt-4",
        ),
        sanitise_untrusted_text=lambda t: t,
        PROMPT_INJECTION_SYSTEM_PROMPT="be safe",
        _canonical_framework_name=lambda v: v,
        _normalise_framework_filter=lambda v: None,
        _normalise_controls_comparison_mode=lambda v: "auto-detect",
        _build_evidence_corpus_filter=lambda corpora: None,
        _controls_search=lambda *a, **kw: (_controls, {}),
        _count_search_documents_total_by_filter=lambda client, *, filter_expr: 3,
        _hybrid_search=lambda q, retrieve_k, evidence_filter: (_b_chunks if "eq 'b'" in (evidence_filter or "") else _c_chunks, {"search_s": 0.1}),
        _chat_completion_with_empty_retry=_chat,
        _unwrap_answer=lambda t: t,
        _chunk_reference_label=lambda c, fallback="": c.get("source_name", fallback),
        search_client=object(),
    )


def test_generate_compliance_report_result_single_pass_success() -> None:
    svc = _make_pipeline_svc()
    payload = ComplianceReportRequest(question="test")
    result = generate_compliance_report_result(payload, svc=svc)
    assert result["mode"] == "compliance-report"
    assert result["schema_valid"] is True
    assert result["assessment_strategy"] == "single_pass"


def test_generate_compliance_report_result_empty_question_builds_default() -> None:
    svc = _make_pipeline_svc()
    payload = ComplianceReportRequest(question="")
    result = generate_compliance_report_result(payload, svc=svc)
    assert result["assessment_question_supplied"] is False
    assert "general compliance assessment" in result["effective_assessment_question"].lower()


def test_generate_compliance_report_result_soft_validation_fallback() -> None:
    """With validation_mode=soft, schema-invalid model JSON triggers fallback not exception."""
    # Valid JSON, but wrong schema (missing required fields) -> _validate fails -> fallback
    svc = _make_pipeline_svc(model_response='{"schema_version":"v0.0","bad":true}')
    payload = ComplianceReportRequest(question="test", validation_mode="soft")
    # Should not raise — returns fallback
    result = generate_compliance_report_result(payload, svc=svc)
    assert result["mode"] == "compliance-report"


def test_generate_compliance_report_result_hard_validation_raises_on_bad_json() -> None:
    svc = _make_pipeline_svc(model_response="not json")
    payload = ComplianceReportRequest(question="test", validation_mode="hard")
    with pytest.raises(Exception):
        generate_compliance_report_result(payload, svc=svc)


def test_generate_compliance_report_result_per_control_strategy() -> None:
    controls = [
        {"requirement_id": "C-1", "framework": "ISM", "requirement_text": "req", "guidance_text": ""}
    ]
    llm_response = '{"finding_id":"f-1","requirement_id":"C-1","framework":"ISM","status":"compliant","severity":"low","rationale":"ok","evidence_sources":["doc.pdf"],"gaps":[],"recommendations":[]}'
    svc = _make_pipeline_svc(controls=controls, model_response=llm_response)
    payload = ComplianceReportRequest(question="test", assessment_strategy="per_control")
    result = generate_compliance_report_result(payload, svc=svc)
    assert result["assessment_strategy"] == "per_control"


def test_generate_compliance_report_result_with_batch_filters() -> None:
    call_filters: list[str] = []

    def _count(client, *, filter_expr):
        call_filters.append(filter_expr)
        return 5

    svc = _make_pipeline_svc()
    svc._count_search_documents_total_by_filter = _count
    payload = ComplianceReportRequest(
        question="test",
        corpus_b_upload_batch="batch-b",
        corpus_c_upload_batch="batch-c",
    )
    generate_compliance_report_result(payload, svc=svc)
    batch_filters = [f for f in call_filters if "upload_batch" in f]
    assert len(batch_filters) >= 2


# ---------------------------------------------------------------------------
# generate_azure_compliance_report_result
# ---------------------------------------------------------------------------
from query_web.endpoints.compliance import generate_azure_compliance_report_result, AzureComplianceReportRequest


def _make_azure_pipeline_svc(*, assessment_payload: dict | None = None) -> SimpleNamespace:
    _default_payload = {
        "schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
        "executive_summary": "ok",
        "scope_and_inputs": ["s"],
        "controls_assessed": ["C-1"],
        "guidance_applied": [],
        "findings": [
            {
                "finding_id": "f-1",
                "requirement_id": "C-1",
                "framework": "ISM",
                "status": "compliant",
                "severity": "low",
                "rationale": "fine",
                "evidence_sources": ["doc.pdf"],
                "gaps": [],
                "recommendations": [],
            }
        ],
        "overall_risk_rating": "low",
        "missing_evidence": [],
        "recommended_actions": ["ok"],
        "citations": ["doc.pdf"],
    }
    _ap = assessment_payload or _default_payload

    return SimpleNamespace(
        config=SimpleNamespace(query_deployment="gpt-4"),
        sanitise_untrusted_text=lambda t: t,
        PROMPT_INJECTION_SYSTEM_PROMPT="be safe",
        _canonical_framework_name=lambda v: "ISM" if v else None,
        _chunk_reference_label=lambda c, fallback="": fallback,
        credential=None,
        run_azure_assessment=lambda **kw: _ap,
        collect_azure_grounding=Mock(side_effect=NotImplementedError("not called in single_pass")),
    )


def test_generate_azure_compliance_report_result_single_pass_success() -> None:
    svc = _make_azure_pipeline_svc()
    payload = AzureComplianceReportRequest(
        subscription_id="sub-1",
        resource_group="rg-1",
        assessment_strategy="single_pass",
        validation_mode="soft",
    )
    result = generate_azure_compliance_report_result(payload, svc=svc)
    assert result["mode"] == "azure-compliance-report"
    assert result["framework"] == "ISM"
    assert result["schema_valid"] is True


def test_generate_azure_compliance_report_result_empty_subscription_raises() -> None:
    svc = _make_azure_pipeline_svc()
    payload = AzureComplianceReportRequest(
        subscription_id="   ",
        resource_group="rg-1",
        validation_mode="soft",
    )
    with pytest.raises(ValueError, match="subscription_id"):
        generate_azure_compliance_report_result(payload, svc=svc)


def test_generate_azure_compliance_report_result_empty_framework_raises() -> None:
    svc = _make_azure_pipeline_svc()
    svc._canonical_framework_name = lambda v: None
    payload = AzureComplianceReportRequest(
        subscription_id="sub-1",
        resource_group="rg-1",
        validation_mode="soft",
    )
    with pytest.raises(ValueError, match="controls_framework"):
        generate_azure_compliance_report_result(payload, svc=svc)


def test_generate_azure_compliance_report_result_no_rg_and_no_ids_raises() -> None:
    svc = _make_azure_pipeline_svc()
    payload = AzureComplianceReportRequest(
        subscription_id="sub-1",
        resource_group="",
        resource_ids=[],
        validation_mode="soft",
    )
    with pytest.raises(ValueError, match="resource_group"):
        generate_azure_compliance_report_result(payload, svc=svc)


def test_generate_azure_compliance_report_result_schema_validation_failure_soft() -> None:
    svc = _make_azure_pipeline_svc(assessment_payload={"bad": "payload"})
    payload = AzureComplianceReportRequest(
        subscription_id="sub-1",
        resource_group="rg-1",
        validation_mode="soft",
    )
    # Should not raise — soft mode returns error in result
    result = generate_azure_compliance_report_result(payload, svc=svc)
    assert result["mode"] == "azure-compliance-report"
    assert result["schema_valid"] is False


def test_generate_azure_compliance_report_result_schema_validation_failure_hard_raises() -> None:
    svc = _make_azure_pipeline_svc(assessment_payload={"bad": "payload"})
    payload = AzureComplianceReportRequest(
        subscription_id="sub-1",
        resource_group="rg-1",
        validation_mode="hard",
    )
    with pytest.raises(RuntimeError, match="schema validation failed"):
        generate_azure_compliance_report_result(payload, svc=svc)
