from __future__ import annotations

import os
from dataclasses import replace
from unittest.mock import patch

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

    with patch.object(app_module, "config", _open_auth_config()), patch.object(
        app_module,
        "_count_search_documents_by_filter",
        side_effect=[{"would_delete": 2}, {"would_delete": 3}],
    ) as count_mock, patch.object(
        app_module, "_delete_search_documents_by_filter"
    ) as delete_mock:
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

    with patch.object(app_module, "config", _open_auth_config()), patch.object(
        app_module,
        "_count_search_documents_by_filter",
        return_value={"would_delete": 7},
    ) as count_index, patch.object(
        app_module,
        "_count_blob_prefix",
        return_value={"would_delete": 4},
    ) as count_blobs, patch.object(
        app_module, "_delete_search_documents_by_filter"
    ) as delete_index, patch.object(
        app_module, "_delete_blob_prefix"
    ) as delete_blobs:
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


def test_compliance_report_soft_mode_returns_validation_details() -> None:
    client = _test_client()

    with patch.object(app_module, "config", _open_auth_config()), patch.object(
        app_module,
        "_controls_search",
        return_value=([], {"controls_search_s": 0.01}),
    ), patch.object(
        app_module,
        "_hybrid_search",
        side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
    ), patch.object(
        app_module,
        "_chat_completion",
        return_value='{"schema_version":"v1.1"}',
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
    assert body["schema_valid"] is False
    assert body["validation_mode"] == "soft"
    assert body["report_structured"] is None
    assert body["validation_error"]


def test_compliance_report_hard_mode_fails_on_schema_mismatch() -> None:
    client = _test_client()

    with patch.object(app_module, "config", _open_auth_config()), patch.object(
        app_module,
        "_controls_search",
        return_value=([], {"controls_search_s": 0.01}),
    ), patch.object(
        app_module,
        "_hybrid_search",
        side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
    ), patch.object(
        app_module,
        "_chat_completion",
        return_value='{"schema_version":"v1.1"}',
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
    assert response.status_code == 500
    assert "schema validation failed" in body["error"].lower()


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
        '}],'
        '"overall_risk_rating":"low",'
        '"missing_evidence":[],'
        '"recommended_actions":["Continue"],'
        '"citations":["REQ-1:doc1"]'
        "}"
    )

    with patch.object(app_module, "config", _open_auth_config()), patch.object(
        app_module,
        "_controls_search",
        return_value=([], {"controls_search_s": 0.01}),
    ), patch.object(
        app_module,
        "_hybrid_search",
        side_effect=[([], {"search_s": 0.01}), ([], {"search_s": 0.02})],
    ), patch.object(
        app_module,
        "_chat_completion",
        return_value=valid_report_json,
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
