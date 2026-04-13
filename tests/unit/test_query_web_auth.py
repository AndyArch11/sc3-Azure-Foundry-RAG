from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from starlette.requests import Request

from query_web import app as app_module
from query_web.app import (
    _group_auth_failure_message,
    _groups_from_client_principal_header,
    _is_authorised_request,
)


def _encode_principal(claims: list[dict[str, str]]) -> str:
    payload = json.dumps({"claims": claims}, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii").rstrip("=")


def _make_request(headers: dict[str, str] | None = None) -> Request:
    header_items = []
    for name, value in (headers or {}).items():
        header_items.append((name.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": header_items,
    }
    return Request(scope)


def test_groups_from_client_principal_header_normalises_claim_values() -> None:
    encoded = _encode_principal(
        [
            {"typ": "groups", "val": "7C110A48-68AC-4514-AE8F-1F674091B559"},
            {
                "typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
                "val": "11111111-1111-1111-1111-111111111111;22222222-2222-2222-2222-222222222222",
            },
        ]
    )

    groups = _groups_from_client_principal_header(encoded)

    assert "7c110a48-68ac-4514-ae8f-1f674091b559" in groups
    assert "11111111-1111-1111-1111-111111111111" in groups
    assert "22222222-2222-2222-2222-222222222222" in groups


def test_is_authorised_request_accepts_case_mismatched_group_ids() -> None:
    request = _make_request(
        {
            "x-ms-client-principal": _encode_principal(
                [{"typ": "groups", "val": "7C110A48-68AC-4514-AE8F-1F674091B559"}]
            )
        }
    )

    patched_config = replace(
        app_module.config,
        required_group_object_id="7c110a48-68ac-4514-ae8f-1f674091b559",
        auth_token="",
    )
    with patch.object(app_module, "config", patched_config):
        assert _is_authorised_request("", request) is True


def test_group_auth_failure_message_reports_missing_principal_headers() -> None:
    request = _make_request()

    message = _group_auth_failure_message(request)

    assert "No Entra ID principal headers were forwarded" in message


def test_group_auth_failure_message_reports_group_overage() -> None:
    request = _make_request(
        {
            "x-ms-client-principal": _encode_principal(
                [
                    {
                        "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                        "val": "abc",
                    },
                    {"typ": "hasgroups", "val": "true"},
                ]
            )
        }
    )

    message = _group_auth_failure_message(request)

    assert "group overage" in message.lower()


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/corpus-a/clear", {"frameworks": ["all"], "dry_run": True, "auth_token": ""}),
        ("/api/corpus-b/clear", {"dry_run": True, "clear_blobs": False, "auth_token": ""}),
        ("/api/corpus-c/clear", {"dry_run": True, "clear_blobs": False, "auth_token": ""}),
    ],
)
def test_clear_endpoints_require_entra_headers_when_group_auth_enabled(
    path: str,
    payload: dict[str, object],
) -> None:
    client = TestClient(app_module.app)
    patched_config = replace(
        app_module.config,
        required_group_object_id="7c110a48-68ac-4514-ae8f-1f674091b559",
        auth_token="",
    )

    with (
        patch.object(app_module, "config", patched_config),
        patch.object(app_module, "_delete_search_documents_by_filter") as delete_index,
        patch.object(app_module, "_delete_blob_prefix") as delete_blobs,
        patch.object(app_module, "_count_search_documents_by_filter") as count_index,
        patch.object(app_module, "_count_blob_prefix") as count_blobs,
    ):
        response = client.post(path, json=payload)

    body = response.json()
    assert response.status_code == 401
    assert "principal headers" in body["error"].lower() or "unauthorized" in body["error"].lower()

    delete_index.assert_not_called()
    delete_blobs.assert_not_called()
    count_index.assert_not_called()
    count_blobs.assert_not_called()


def test_clear_endpoint_allows_valid_group_header_in_dry_run_mode() -> None:
    client = TestClient(app_module.app)
    required_group = "7c110a48-68ac-4514-ae8f-1f674091b559"
    patched_config = replace(
        app_module.config,
        required_group_object_id=required_group,
        auth_token="",
    )
    principal_header = _encode_principal([{"typ": "groups", "val": required_group.upper()}])

    with (
        patch.object(app_module, "config", patched_config),
        patch.object(
            app_module,
            "_count_search_documents_by_filter",
            return_value={"would_delete": 5},
        ) as count_index,
        patch.object(app_module, "_delete_search_documents_by_filter") as delete_index,
    ):
        response = client.post(
            "/api/corpus-b/clear",
            json={"dry_run": True, "clear_blobs": False, "auth_token": ""},
            headers={"x-ms-client-principal": principal_header},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["dry_run"] is True
    assert body["index"]["would_delete"] == 5
    count_index.assert_called_once_with(app_module.search_client, filter_expr="corpus eq 'b'")
    delete_index.assert_not_called()


def test_confluence_poll_status_returns_persisted_poll_data() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(app_module.config, required_group_object_id="", auth_token="")

    class _FakeStore:
        def get_latest_poll_run_summary(self, source: str):
            assert source == "confluence"

            class _Summary:
                polled_at = "2026-04-13T02:00:00+00:00"
                space_keys = ("SEC", "GRC")
                mentions_found = 3
                jobs_queued = 2
                terminal_failures = 1
                error_message = ""
                watermark = "2026-04-13T02:00:00+00:00"
                since_iso = "2026-04-13T01:00:00+00:00"

            return _Summary()

        def list_recent_page_assessments(self, source: str, *, since_iso: str, limit: int = 100):
            assert source == "confluence"
            assert since_iso
            assert limit == 200

            class _Record:
                target_id = "123"
                title = "Confluence Page"
                target_url = "https://example/wiki/pages/123"
                space_key = "SEC"
                overall_risk = "high"
                assessed_at = "2026-04-13T02:01:00+00:00"
                framework_scope = "NIST CSF"
                findings_count = 4
                status = "assessed"
                page_version = "7"

            return [_Record()]

        def list_recent_failures(self, source: str, *, since_iso: str, limit: int = 50):
            assert source == "confluence"
            assert since_iso
            assert limit == 50

            class _Failure:
                event_id = "evt-9"
                status = "failed_terminal"
                attempt_count = 3
                last_error = "boom"
                last_attempt_at = "2026-04-13T02:02:00+00:00"
                run_id = "run-1"

            return [_Failure()]

    with (
        patch.object(app_module, "config", patched_config),
        patch.object(app_module, "confluence_poll_state_store", _FakeStore()),
    ):
        response = client.get("/api/confluence/poll-status?since_hours=12")

    body = response.json()
    assert response.status_code == 200
    assert body["configured"] is True
    assert body["last_poll"]["mentions_found"] == 3
    assert body["last_poll"]["space_key"] == "SEC, GRC"
    assert body["summary"]["page_status_counts"]["assessed"] == 1
    assert body["summary"]["failure_status_counts"]["failed_terminal"] == 1
    assert len(body["assessed_pages"]) == 1
    assert body["assessed_pages"][0]["overall_risk"] == "High"
    assert body["assessed_pages"][0]["framework"] == "NIST CSF"
    assert len(body["recent_failures"]) == 1
    assert body["recent_failures"][0]["last_error"] == "boom"


def test_confluence_poll_status_degrades_gracefully_when_store_reads_fail() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(app_module.config, required_group_object_id="", auth_token="")

    class _FailingStore:
        def get_latest_poll_run_summary(self, source: str):
            raise RuntimeError("summary query failed")

        def list_recent_page_assessments(self, source: str, *, since_iso: str, limit: int = 100):
            raise RuntimeError("assessment query failed")

        def list_recent_failures(self, source: str, *, since_iso: str, limit: int = 50):
            raise RuntimeError("failure query failed")

    with (
        patch.object(app_module, "config", patched_config),
        patch.object(app_module, "confluence_poll_state_store", _FailingStore()),
    ):
        response = client.get("/api/confluence/poll-status?since_hours=12")

    body = response.json()
    assert response.status_code == 200
    assert body["configured"] is False
    assert "unavailable" in body["message"]
    assert body["assessed_pages"] == []


def test_confluence_poll_status_falls_back_to_poll_state_when_summary_missing() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(app_module.config, required_group_object_id="", auth_token="")

    class _StateOnlyStore:
        def get_latest_poll_run_summary(self, source: str):
            return None

        def load_state(self, source: str):
            class _State:
                watermark = "2026-04-13T03:00:00+00:00"
                last_success_at = "2026-04-13T03:00:00+00:00"
                last_processed_event_id = "evt-123"
                poll_count = 5
                last_error = {}

            return _State()

        def list_recent_page_assessments(self, source: str, *, since_iso: str, limit: int = 100):
            return []

        def list_recent_failures(self, source: str, *, since_iso: str, limit: int = 50):
            return []

    with (
        patch.object(app_module, "config", patched_config),
        patch.object(app_module, "confluence_poll_state_store", _StateOnlyStore()),
    ):
        response = client.get("/api/confluence/poll-status?since_hours=12")

    body = response.json()
    assert response.status_code == 200
    assert body["configured"] is True
    assert body["last_poll"]["polled_at"] == "2026-04-13T03:00:00+00:00"
    assert body["last_poll"]["last_processed_event_id"] == "evt-123"
    assert body["last_poll"]["poll_count"] == 5
