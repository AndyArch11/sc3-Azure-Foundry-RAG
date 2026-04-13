from __future__ import annotations

import os
from dataclasses import replace
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web import app as app_module


def _test_client() -> TestClient:
    return TestClient(app_module.app)


def _open_auth_config():
    return replace(app_module.config, auth_token="", required_group_object_id="")


def test_corpus_a_clear_dry_run_returns_would_delete_counts() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_count_search_documents_by_filter",
            side_effect=[{"would_delete": 2}, {"would_delete": 3}],
        ) as count_mock,
        patch.object(app_module, "_delete_search_documents_by_filter") as delete_mock,
    ):
        response = client.post(
            "/api/corpus-a/clear",
            json={
                "frameworks": ["essential_eight", "nist_csf"],
                "dry_run": True,
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["dry_run"] is True
    assert body["total_would_delete"] == 5
    assert body["total_deleted"] == 0
    assert count_mock.call_count == 2
    delete_mock.assert_not_called()


def test_corpus_b_clear_dry_run_uses_count_paths() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_count_search_documents_by_filter",
            return_value={"would_delete": 7},
        ) as count_index,
        patch.object(
            app_module,
            "_count_blob_prefix",
            return_value={"would_delete": 4},
        ) as count_blobs,
        patch.object(app_module, "_delete_search_documents_by_filter") as delete_index,
        patch.object(app_module, "_delete_blob_prefix") as delete_blobs,
    ):
        response = client.post(
            "/api/corpus-b/clear",
            json={"dry_run": True, "clear_blobs": True, "auth_token": ""},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["dry_run"] is True
    assert body["index"]["would_delete"] == 7
    assert body["blobs"]["would_delete"] == 4
    count_index.assert_called_once()
    count_blobs.assert_called_once_with("corpus-b/by-dedupe/")
    delete_index.assert_not_called()
    delete_blobs.assert_not_called()


def test_compliance_report_soft_mode_normalises_incomplete_payload() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=([], {"controls_search_s": 0.01}),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            return_value='{"schema_version":"v1.1"}',
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "validation_mode": "soft",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert body["validation_mode"] == "soft"
    assert body["report_structured"] is not None
    assert body["validation_error"] == ""


def test_compliance_report_hard_mode_normalises_incomplete_payload() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=([], {"controls_search_s": 0.01}),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            return_value='{"schema_version":"v1.1"}',
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "validation_mode": "hard",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert body["report_structured"] is not None


def test_compliance_report_valid_schema_returns_csv() -> None:
    client = _test_client()

    valid_report_json = (
        "{"
        '"schema_version":"v1.1",'
        '"executive_summary":"Summary",'
        '"scope_and_inputs":["Corpus A","Corpus B","Corpus C"],'
        '"controls_assessed":["REQ-1"],'
        '"guidance_applied":["Guide 1"],'
        '"findings":[{'
        '"finding_id":"F-1",'
        '"requirement_id":"REQ-1",'
        '"framework":"NIST CSF",'
        '"status":"compliant",'
        '"severity":"low",'
        '"rationale":"Met",'
        '"evidence_sources":["doc1"],'
        '"gaps":[], '
        '"recommendations":["Keep monitoring"]'
        "}],"
        '"overall_risk_rating":"low",'
        '"missing_evidence":[],'
        '"recommended_actions":["Continue"],'
        '"citations":["REQ-1:doc1"]'
        "}"
    )

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=([], {"controls_search_s": 0.01}),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            return_value=valid_report_json,
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "validation_mode": "hard",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert body["report_structured"]["schema_version"] == "v1.1"
    assert "finding_id,requirement_id,framework,status,severity" in body["report_findings_csv"]


def test_compliance_report_retries_after_empty_model_response() -> None:
    client = _test_client()

    valid_report_json = (
        "{"
        '"schema_version":"v1.1",'
        '"executive_summary":"Summary",'
        '"scope_and_inputs":["Corpus A","Corpus B","Corpus C"],'
        '"controls_assessed":["REQ-1"],'
        '"guidance_applied":["Guide 1"],'
        '"findings":[{'
        '"finding_id":"F-1",'
        '"requirement_id":"REQ-1",'
        '"framework":"NIST CSF",'
        '"status":"compliant",'
        '"severity":"low",'
        '"rationale":"Met",'
        '"evidence_sources":["doc1"],'
        '"gaps":[], '
        '"recommendations":["Keep monitoring"]'
        "}],"
        '"overall_risk_rating":"low",'
        '"missing_evidence":[],'
        '"recommended_actions":["Continue"],'
        '"citations":["REQ-1:doc1"]'
        "}"
    )

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=([], {"controls_search_s": 0.01}),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            side_effect=["", valid_report_json],
        ) as completion_mock,
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "validation_mode": "hard",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert completion_mock.call_count == 2


def test_compliance_report_normalises_incomplete_model_json() -> None:
    client = _test_client()

    incomplete_report_json = (
        "{"
        '"schema_version":"v1.1",'
        '"executive_summary":"Draft summary",'
        '"scope_and_inputs":["Corpus A","Corpus B","Corpus C"],'
        '"controls_assessed":[],'
        '"guidance_applied":[],'
        '"findings":[{'
        '"finding_id":"F-1",'
        '"requirement_id":"",'
        '"framework":"",'
        '"status":"insufficient_evidence",'
        '"severity":"medium",'
        '"rationale":"Need more evidence",'
        '"evidence_sources":[],'
        '"gaps":[], '
        '"recommendations":[]'
        "}],"
        '"overall_risk_rating":"medium",'
        '"missing_evidence":[], '
        '"recommended_actions":[], '
        '"citations":[]'
        "}"
    )

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=(
                [
                    {
                        "requirement_id": "REQ-1",
                        "framework": "NIST CSF",
                        "framework_version": "2.0",
                        "control_family": "Access Control",
                        "requirement_text": "Use MFA",
                        "guidance_text": "Apply MFA broadly",
                        "source_uri": "controls://req-1",
                    }
                ],
                {"controls_search_s": 0.01},
            ),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[
                ([{"source_name": "Guide-A", "content": "guidance"}], {"search_s": 0.01}),
                ([{"source_name": "Artifact-1", "content": "artifact"}], {"search_s": 0.02}),
            ],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            return_value=incomplete_report_json,
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "validation_mode": "hard",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert body["report_structured"]["controls_assessed"]
    assert body["report_structured"]["findings"][0]["requirement_id"]
    assert body["report_structured"]["findings"][0]["framework"]
    assert body["report_structured"]["findings"][0]["evidence_sources"]


def test_compliance_report_corrects_model_claims_when_grounding_exists() -> None:
    client = _test_client()

    contradictory_report_json = (
        "{"
        '"schema_version":"v1.1",'
        '"executive_summary":"No normative requirements or assessed artifacts were available.",'
        '"scope_and_inputs":["Corpus A: No controls retrieved"],'
        '"controls_assessed":[],'
        '"guidance_applied":[],'
        '"findings":[{'
        '"finding_id":"F-1",'
        '"requirement_id":"",'
        '"framework":"",'
        '"status":"insufficient_evidence",'
        '"severity":"high",'
        '"rationale":"No evidence",'
        '"evidence_sources":[],'
        '"gaps":[], '
        '"recommendations":[]'
        "}],"
        '"overall_risk_rating":"high",'
        '"missing_evidence":[], '
        '"recommended_actions":[], '
        '"citations":[]'
        "}"
    )

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=(
                [
                    {
                        "requirement_id": "REQ-99",
                        "framework": "NIST CSF",
                        "framework_version": "2.0",
                        "control_family": "Protect",
                        "requirement_text": "Control text",
                        "guidance_text": "Guidance text",
                        "source_uri": "controls://req-99",
                    }
                ],
                {"controls_search_s": 0.01},
            ),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[
                ([{"source_name": "Guide-X", "content": "guidance"}], {"search_s": 0.01}),
                ([{"source_name": "Artifact-X", "content": "artifact"}], {"search_s": 0.02}),
            ],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            return_value=contradictory_report_json,
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "validation_mode": "hard",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert body["report_structured"]["controls_assessed"] == ["REQ-99"]
    assert body["report_structured"]["scope_and_inputs"] == [
        "Corpus A controls retrieved: 1",
        "Corpus B guidance retrieved: 1",
        "Corpus C artifacts retrieved: 1",
    ]


def test_compliance_report_soft_mode_returns_fallback_report_on_empty_model_output() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=(
                [
                    {
                        "requirement_id": "REQ-7",
                        "framework": "NIST CSF",
                        "framework_version": "2.0",
                        "control_family": "Identify",
                        "requirement_text": "Inventory assets",
                        "guidance_text": "Maintain inventory",
                        "source_uri": "controls://req-7",
                    }
                ],
                {"controls_search_s": 0.01},
            ),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[
                ([], {"search_s": 0.01}),
                ([{"source_name": "Artifact-Z", "content": "artifact"}], {"search_s": 0.02}),
            ],
        ),
        patch.object(
            app_module,
            "_chat_completion",
            side_effect=["", ""],
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess artifact coverage.",
                "validation_mode": "soft",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is False
    assert body["report_structured"] is not None
    assert body["report"]
    assert body["report_structured"]["controls_assessed"] == ["REQ-7"]


def test_compliance_report_uses_corpus_b_upload_batch_filter() -> None:
    client = _test_client()

    valid_report_json = (
        "{"
        '"schema_version":"v1.1",'
        '"executive_summary":"Summary",'
        '"scope_and_inputs":["Corpus A","Corpus B","Corpus C"],'
        '"controls_assessed":["REQ-1"],'
        '"guidance_applied":["Guide 1"],'
        '"findings":[{'
        '"finding_id":"F-1",'
        '"requirement_id":"REQ-1",'
        '"framework":"NIST CSF",'
        '"status":"compliant",'
        '"severity":"low",'
        '"rationale":"Met",'
        '"evidence_sources":["doc1"],'
        '"gaps":[], '
        '"recommendations":["Keep monitoring"]'
        "}],"
        '"overall_risk_rating":"low",'
        '"missing_evidence":[], '
        '"recommended_actions":["Continue"], '
        '"citations":["REQ-1:doc1"]'
        "}"
    )

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_controls_search",
            return_value=([], {"controls_search_s": 0.01}),
        ),
        patch.object(
            app_module,
            "_hybrid_search",
            side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
        ) as hybrid_mock,
        patch.object(
            app_module,
            "_chat_completion",
            return_value=valid_report_json,
        ),
    ):
        response = client.post(
            "/api/compliance/report",
            json={
                "question": "Assess control coverage.",
                "corpus_b_upload_batch": "batch-b-123",
                "validation_mode": "hard",
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["schema_valid"] is True
    assert hybrid_mock.call_count == 2
    first_call_kwargs = hybrid_mock.call_args_list[0].kwargs
    assert first_call_kwargs["evidence_filter"] == "corpus eq 'b' and upload_batch eq 'batch-b-123'"


def test_assess_control_finding_coerces_scalar_list_fields() -> None:
    with patch.object(
        app_module,
        "_chat_completion_with_empty_retry",
        return_value=(
            '{"finding_id":"F-1","requirement_id":"REQ-1","framework":"NIST CSF",'
            '"status":"insufficient_evidence","severity":"medium","rationale":"Need more evidence",'
            '"evidence_sources":"Artifact-A","gaps":"Missing proof","recommendations":"Collect logs"}'
        ),
    ):
        finding = app_module._assess_control_finding_with_llm(
            question="Which frameworks require MFA?",
            control={
                "requirement_id": "REQ-1",
                "framework": "NIST CSF",
                "control_family": "Access",
                "requirement_text": "MFA is required.",
                "guidance_text": "",
            },
            corpus_b_chunks=[],
            corpus_c_chunks=[],
            temperature=0.2,
        )

    assert finding["evidence_sources"] == ["Artifact-A"]
    assert finding["gaps"] == ["Missing proof"]
    assert finding["recommendations"] == ["Collect logs"]


def test_hybrid_search_returns_empty_results_when_embedding_is_rate_limited() -> None:
    response = requests.Response()
    response.status_code = 429
    error = requests.HTTPError("429 Too Many Requests", response=response)

    with patch.object(app_module, "_embed_query", side_effect=error):
        items, timings = app_module._hybrid_search(
            question="Which frameworks require MFA?",
            retrieve_k=5,
        )

    assert items == []
    assert timings.get("embedding_rate_limited") == 1.0
    assert timings.get("search_s") == 0.0


def test_corpus_b_ingest_does_not_trigger_job_when_all_files_are_duplicates() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_upload_corpus_b_files",
            return_value={
                "upload_batch_id": None,
                "prefix": "corpus-b/by-dedupe",
                "uploaded": [],
                "skipped": ["duplicate.html: duplicate-normalised_text_sha256:abc"],
                "failed": [],
            },
        ),
        patch.object(app_module, "_trigger_ingestion_job") as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-b/ingest",
            data={"trigger_job": "true", "auth_token": ""},
            files={"files": ("duplicate.html", b"test content", "text/html")},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["uploaded_count"] == 0
    assert body["triggered_job"] is False
    assert body["job"] is None
    assert body["upload"]["upload_batch_id"] is None
    assert "no ingestion job was triggered" in body["message"].lower()
    trigger_mock.assert_not_called()


def test_corpus_c_ingest_does_not_trigger_job_when_all_files_are_duplicates() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_upload_corpus_files",
            return_value={
                "upload_batch_id": None,
                "prefix": "corpus-c/by-dedupe",
                "uploaded": [],
                "skipped": ["duplicate.html: duplicate-normalised_text_sha256:def"],
                "failed": [],
            },
        ),
        patch.object(app_module, "_trigger_ingestion_job") as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-c/ingest",
            data={"trigger_job": "true", "auth_token": ""},
            files={"files": ("duplicate.html", b"test content", "text/html")},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["uploaded_count"] == 0
    assert body["triggered_job"] is False
    assert body["job"] is None
    assert body["upload"]["upload_batch_id"] is None
    assert "no ingestion job was triggered" in body["message"].lower()
    trigger_mock.assert_not_called()


def test_corpus_b_ingest_triggers_job_when_reindex_on_dedupe_enabled() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_upload_corpus_b_files",
            return_value={
                "upload_batch_id": None,
                "prefix": "corpus-b/by-dedupe",
                "uploaded": [],
                "skipped": ["duplicate.html: duplicate-normalised_text_sha256:abc"],
                "failed": [],
            },
        ),
        patch.object(
            app_module,
            "_reset_grounding_indexer_state",
            return_value="grounding-index-indexer",
        ),
        patch.object(
            app_module,
            "_trigger_ingestion_job",
            return_value={"status_code": 202},
        ) as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-b/ingest",
            data={
                "trigger_job": "true",
                "reindex_on_dedupe": "true",
                "auth_token": "",
            },
            files={"files": ("duplicate.html", b"test content", "text/html")},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["uploaded_count"] == 0
    assert body["triggered_job"] is True
    assert body["reindex_on_dedupe"] is True
    assert body["indexer_reset"]["performed"] is True
    assert "re-index existing blobs" in body["message"].lower()
    trigger_mock.assert_called_once()


def test_corpus_c_ingest_triggers_job_when_reindex_on_dedupe_enabled() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_upload_corpus_files",
            return_value={
                "upload_batch_id": None,
                "prefix": "corpus-c/by-dedupe",
                "uploaded": [],
                "skipped": ["duplicate.html: duplicate-normalised_text_sha256:def"],
                "failed": [],
            },
        ),
        patch.object(
            app_module,
            "_reset_grounding_indexer_state",
            return_value="grounding-index-indexer",
        ),
        patch.object(
            app_module,
            "_trigger_ingestion_job",
            return_value={"status_code": 202},
        ) as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-c/ingest",
            data={
                "trigger_job": "true",
                "reindex_on_dedupe": "true",
                "auth_token": "",
            },
            files={"files": ("duplicate.html", b"test content", "text/html")},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["uploaded_count"] == 0
    assert body["triggered_job"] is True
    assert body["reindex_on_dedupe"] is True
    assert body["indexer_reset"]["performed"] is True
    assert "re-index existing blobs" in body["message"].lower()
    trigger_mock.assert_called_once()


def test_corpus_a_list_with_framework_filter() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_list_search_documents_by_filter",
            return_value={
                "total_count": 2,
                "returned_count": 2,
                "items": [{"requirement_id": "REQ-1"}],
            },
        ) as list_mock,
    ):
        response = client.get(
            "/api/corpus-a/list",
            params={"framework": "nist_csf", "limit": 25, "auth_token": ""},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "corpus-a-list"
    assert body["framework_filter"] == "nist_csf"
    assert body["returned_count"] == 2

    call_kwargs = list_mock.call_args.kwargs
    assert call_kwargs["filter_expr"] == "framework eq 'NIST CSF'"
    assert call_kwargs["limit"] == 25


def test_corpus_a_upload_stages_sources_and_triggers_controls_job() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_upload_corpus_a_reference_files",
            return_value={
                "framework": "cis_controls",
                "framework_name": "CIS Controls",
                "upload_batch_id": "batch-a-1",
                "source_prefix": "corpus-a/source/cis_controls/batch-a-1",
                "uploaded": [
                    {"target_filename": "CIS_Controls_Version_8.xlsx"},
                    {
                        "target_filename": "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf"
                    },
                ],
                "failed": [],
            },
        ),
        patch.object(
            app_module,
            "_trigger_ingestion_job_with_args",
            return_value={"status_code": 200, "args_override": []},
        ) as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-a/upload",
            data={
                "framework": "cis_controls",
                "trigger_job": "true",
                "replace_existing": "true",
                "dry_run": "true",
                "no_guidance": "true",
                "auth_token": "",
            },
            files=[
                (
                    "files",
                    (
                        "controls.xlsx",
                        b"xlsx-bytes",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
                ("files", ("controls.pdf", b"pdf-bytes", "application/pdf")),
            ],
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "corpus-a-upload"
    assert body["framework"] == "cis_controls"
    assert body["uploaded_count"] == 2
    assert body["triggered_job"] is True

    args_override = trigger_mock.call_args.args[0]
    assert "--controls-framework" in args_override
    assert "cis_controls" in args_override
    assert "--controls-source-prefix" in args_override
    assert "corpus-a/source/cis_controls/batch-a-1" in args_override
    assert "--replace-existing" in args_override
    assert "--dry-run" in args_override
    assert "--no-guidance" in args_override


def test_corpus_a_upload_rejects_unsupported_framework() -> None:
    client = _test_client()

    with patch.object(app_module, "config", _open_auth_config()):
        response = client.post(
            "/api/corpus-a/upload",
            data={
                "framework": "nist_csf",
                "trigger_job": "true",
                "auth_token": "",
            },
            files=[("files", ("controls.pdf", b"pdf-bytes", "application/pdf"))],
        )

    body = response.json()
    assert response.status_code == 400
    assert "supports cis_controls, pci_dss, or auto mode" in body["error"]


def test_corpus_a_upload_auto_stages_multiple_frameworks_and_triggers_jobs() -> None:
    client = _test_client()

    def _fake_upload(files, user_id, *, framework):
        if framework == "cis_controls":
            return {
                "framework": "cis_controls",
                "framework_name": "CIS Controls",
                "upload_batch_id": "batch-cis-1",
                "source_prefix": "corpus-a/source/cis_controls/batch-cis-1",
                "uploaded": [{"target_filename": "CIS_Controls_Version_8.xlsx"}],
                "failed": [],
            }
        if framework == "pci_dss":
            return {
                "framework": "pci_dss",
                "framework_name": "PCI DSS",
                "upload_batch_id": "batch-pci-1",
                "source_prefix": "corpus-a/source/pci_dss/batch-pci-1",
                "uploaded": [{"target_filename": "PCI-DSS-v4_0_1.pdf"}],
                "failed": [],
            }
        raise AssertionError("unexpected framework")

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_upload_corpus_a_reference_files",
            side_effect=_fake_upload,
        ) as upload_mock,
        patch.object(
            app_module,
            "_trigger_ingestion_job_with_args",
            return_value={"status_code": 200, "args_override": []},
        ) as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-a/upload",
            data={
                "framework": "auto",
                "trigger_job": "true",
                "auth_token": "",
            },
            files=[
                (
                    "files",
                    (
                        "CIS_Controls_Version_8.xlsx",
                        b"xlsx-bytes",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
                (
                    "files",
                    (
                        "PCI-DSS-v4_0_1.pdf",
                        b"pdf-bytes",
                        "application/pdf",
                    ),
                ),
            ],
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "corpus-a-upload"
    assert body["framework"] == "auto"
    assert body["uploaded_count"] == 2
    assert body["triggered_job"] is True
    assert len(body["uploads"]) == 2
    assert len(body["jobs"]) == 2
    assert upload_mock.call_count == 2
    assert trigger_mock.call_count == 2


def test_corpus_a_ingest_skips_frameworks_requiring_source_upload() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_is_ingestion_job_trigger_enabled",
            return_value=True,
        ),
        patch.object(
            app_module,
            "_controls_framework_ingestion_status",
            return_value={},
        ),
        patch.object(
            app_module,
            "_trigger_ingestion_job_with_args",
            return_value={"status_code": 200, "args_override": []},
        ) as trigger_mock,
    ):
        response = client.post(
            "/api/corpus-a/ingest",
            json={
                "frameworks": ["cis_controls", "pci_dss", "nist_csf"],
                "replace_existing": False,
                "dry_run": False,
                "no_guidance": False,
                "auth_token": "",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert set(body["source_upload_required_frameworks"]) == {"cis_controls", "pci_dss"}
    assert [item["framework"] for item in body["triggered"]] == ["nist_csf"]

    skipped_reasons = {
        item["framework"]: item["reason"]
        for item in body["skipped"]
        if item.get("reason") == "source_upload_required"
    }
    assert skipped_reasons == {
        "cis_controls": "source_upload_required",
        "pci_dss": "source_upload_required",
    }
    trigger_mock.assert_called_once()


def test_corpus_b_list_with_upload_batch_filter() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_list_search_documents_by_filter",
            return_value={"total_count": 1, "returned_count": 1, "items": [{"id": "doc-1"}]},
        ) as list_mock,
    ):
        response = client.get(
            "/api/corpus-b/list",
            params={"upload_batch": "batch-b-1", "limit": 10, "auth_token": ""},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "corpus-b-list"
    assert body["upload_batch_filter"] == "batch-b-1"
    assert body["returned_count"] == 1

    call_kwargs = list_mock.call_args.kwargs
    assert call_kwargs["filter_expr"] == "corpus eq 'b' and upload_batch eq 'batch-b-1'"
    assert call_kwargs["limit"] == 10


def test_corpus_c_list_without_upload_batch_filter() -> None:
    client = _test_client()

    with (
        patch.object(app_module, "config", _open_auth_config()),
        patch.object(
            app_module,
            "_list_search_documents_by_filter",
            return_value={"total_count": 3, "returned_count": 3, "items": [{"id": "doc-3"}]},
        ) as list_mock,
    ):
        response = client.get(
            "/api/corpus-c/list",
            params={"limit": 50, "auth_token": ""},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "corpus-c-list"
    assert body["upload_batch_filter"] is None
    assert body["returned_count"] == 3

    call_kwargs = list_mock.call_args.kwargs
    assert call_kwargs["filter_expr"] == "corpus eq 'c'"
    assert call_kwargs["limit"] == 50
