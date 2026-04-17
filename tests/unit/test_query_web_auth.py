from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
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


def test_search_resources_diagnostics_blocked_when_target_env_prod() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(app_module.config, required_group_object_id="", auth_token="")

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "prod"}, clear=False),
    ):
        response = client.get("/api/diagnostics/search/resources")

    body = response.json()
    assert response.status_code == 403
    assert "disabled" in body["error"].lower()
    assert body["target_env"] == "prod"


def test_search_resources_diagnostics_returns_resource_summary_in_dev() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(app_module.config, required_group_object_id="", auth_token="")

    class _FakeSearchIndexClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def list_indexes(self):
            return [SimpleNamespace(name="grounding-index")]

    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def list_data_source_connections(self):
            container = SimpleNamespace(name="grounding-data", query="corpus-b/by-dedupe/")
            return [SimpleNamespace(name="grounding-index-datasource", type="azureblob", container=container)]

        def list_skillsets(self):
            return [SimpleNamespace(name="grounding-index-skillset", skills=[object(), object()])]

        def list_indexers(self):
            return [
                SimpleNamespace(
                    name="grounding-index-indexer",
                    data_source_name="grounding-index-datasource",
                    target_index_name="grounding-index",
                    skillset_name="grounding-index-skillset",
                )
            ]

        def get_indexer_status(self, name: str):
            assert name == "grounding-index-indexer"
            last_result = SimpleNamespace(
                status="success",
                item_count=7,
                failed_item_count=0,
                error_message=None,
            )
            return SimpleNamespace(last_result=last_result)

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "dev"}, clear=False),
        patch.object(app_module, "SearchIndexClient", _FakeSearchIndexClient),
        patch.object(app_module, "SearchIndexerClient", _FakeSearchIndexerClient),
    ):
        response = client.get("/api/diagnostics/search/resources")

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "search-resources-diagnostics"
    assert body["target_env"] == "dev"
    assert body["indexes"][0]["name"] == "grounding-index"
    assert body["data_sources"][0]["container"]["query"] == "corpus-b/by-dedupe/"
    assert body["skillsets"][0]["skill_count"] == 2
    assert body["indexers"][0]["last_result"]["status"] == "success"
    assert body["indexers"][0]["last_result"]["items_processed"] == 7


def test_storage_blobs_diagnostics_blocked_when_target_env_prod() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(
        app_module.config,
        required_group_object_id="",
        auth_token="",
        storage_account_name="stdev",
        storage_container_name="grounding-data",
    )

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "prod"}, clear=False),
    ):
        response = client.get("/api/diagnostics/storage/blobs")

    body = response.json()
    assert response.status_code == 403
    assert "disabled" in body["error"].lower()
    assert body["target_env"] == "prod"


def test_storage_blobs_diagnostics_returns_blob_inventory_in_dev() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(
        app_module.config,
        required_group_object_id="",
        auth_token="",
        storage_account_name="stdev",
        storage_container_name="grounding-data",
    )

    class _FakeContainer:
        def list_blobs(self, name_starts_with: str | None = None):
            assert name_starts_with == "corpus-b/by-dedupe/"
            yield SimpleNamespace(
                name="corpus-b/by-dedupe/hash1.pdf",
                size=1234,
                content_settings=SimpleNamespace(content_type="application/pdf"),
                last_modified="2026-04-17T09:00:00+00:00",
                etag='"etag-1"',
                metadata={"corpus": "b", "upload_batch": "batch-1"},
            )
            yield SimpleNamespace(
                name="corpus-b/by-dedupe/hash2.docx",
                size=2345,
                content_settings=SimpleNamespace(content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                last_modified="2026-04-17T09:01:00+00:00",
                etag='"etag-2"',
                metadata={"corpus": "b", "upload_batch": "batch-2"},
            )

    class _FakeBlobServiceClient:
        def __init__(self, account_url: str, credential: object):
            self.account_url = account_url
            self.credential = credential

        def get_container_client(self, container_name: str):
            assert container_name == "grounding-data"
            return _FakeContainer()

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "dev"}, clear=False),
        patch.object(app_module, "BlobServiceClient", _FakeBlobServiceClient),
    ):
        response = client.get(
            "/api/diagnostics/storage/blobs?prefix=corpus-b/by-dedupe/&limit=10&include_metadata=true"
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "storage-blobs-diagnostics"
    assert body["target_env"] == "dev"
    assert body["storage_container_name"] == "grounding-data"
    assert body["prefix"] == "corpus-b/by-dedupe/"
    assert body["returned"] == 2
    assert body["truncated"] is False
    assert body["blobs"][0]["name"] == "corpus-b/by-dedupe/hash1.pdf"
    assert body["blobs"][0]["metadata"]["upload_batch"] == "batch-1"


def test_ingestion_overview_diagnostics_blocked_when_target_env_prod() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(
        app_module.config,
        required_group_object_id="",
        auth_token="",
        storage_account_name="stdev",
        storage_container_name="grounding-data",
    )

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "prod"}, clear=False),
    ):
        response = client.get("/api/diagnostics/ingestion/overview")

    body = response.json()
    assert response.status_code == 403
    assert "disabled" in body["error"].lower()
    assert body["target_env"] == "prod"


def test_ingestion_overview_diagnostics_returns_aggregate_snapshot_in_dev() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(
        app_module.config,
        required_group_object_id="",
        auth_token="",
        storage_account_name="stdev",
        storage_container_name="grounding-data",
        ingestion_job_subscription_id="sub",
        ingestion_job_resource_group="rg",
        ingestion_job_name="job-ingestion",
    )

    class _FakeContainer:
        def list_blobs(self, name_starts_with: str | None = None):
            prefix = name_starts_with or ""
            if prefix == "corpus-a/source/":
                yield SimpleNamespace(
                    name="corpus-a/source/nist/batch1/file1.jsonl",
                    size=100,
                    last_modified="2026-04-17T10:00:00+00:00",
                    metadata={"corpus": "a", "upload_batch": "batch1"},
                )
            elif prefix == "corpus-b/by-dedupe/":
                yield SimpleNamespace(
                    name="corpus-b/by-dedupe/hash-b.pdf",
                    size=200,
                    last_modified="2026-04-17T10:01:00+00:00",
                    metadata={"corpus": "b", "upload_batch": "batch-b"},
                )
            elif prefix == "corpus-c/by-dedupe/":
                yield SimpleNamespace(
                    name="corpus-c/by-dedupe/hash-c.pdf",
                    size=300,
                    last_modified="2026-04-17T10:02:00+00:00",
                    metadata={"corpus": "c", "upload_batch": "batch-c"},
                )

    class _FakeBlobServiceClient:
        def __init__(self, account_url: str, credential: object):
            self.account_url = account_url
            self.credential = credential

        def get_container_client(self, container_name: str):
            assert container_name == "grounding-data"
            return _FakeContainer()

    class _FakePager:
        def __init__(self, count: int):
            self._count = count
            self._yielded = False

        def __iter__(self):
            if not self._yielded:
                self._yielded = True
                yield {"id": "1"}
            return

        def get_count(self):
            return self._count

    class _FakeSearchClient:
        def search(self, **kwargs):
            assert kwargs.get("include_total_count") is True
            return _FakePager(9)

    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_data_source_connection(self, name: str):
            assert name == "grounding-index-datasource"
            # Empty query implies whole-container scan risk in shared-container setups.
            return SimpleNamespace(container=SimpleNamespace(query=""))

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "dev"}, clear=False),
        patch.object(app_module, "BlobServiceClient", _FakeBlobServiceClient),
        patch.object(app_module, "SearchIndexerClient", _FakeSearchIndexerClient),
        patch.object(app_module, "search_client", _FakeSearchClient()),
        patch.object(app_module, "_count_search_documents_total_by_filter", side_effect=[4, 2, 1]),
        patch.object(
            app_module,
            "_count_blob_prefix",
            side_effect=[{"would_delete": 1}, {"would_delete": 5}, {"would_delete": 0}],
        ),
        patch.object(
            app_module,
            "_latest_ingestion_job_execution",
            return_value={"name": "job-exec-1", "status": "Succeeded"},
        ),
    ):
        response = client.get("/api/diagnostics/ingestion/overview?sample_limit=9")

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "ingestion-overview-diagnostics"
    assert body["target_env"] == "dev"
    assert body["search_counts"]["grounding_total"] == 9
    assert body["search_counts"]["corpus_b"] == 4
    assert body["storage_counts"]["corpus_b_dedupe"] == 5
    assert body["latest_ingestion_job"]["status"] == "Succeeded"
    assert body["quick_flags"]["storage_has_corpus_b_but_search_corpus_b_empty"] is False
    assert body["scope_query_diagnostics"]["configured_data_source_name"] == "grounding-index-datasource"
    assert body["scope_query_diagnostics"]["active_data_source_query"] is None
    assert body["scope_query_diagnostics"]["scope_bleed_risk_level"] == "high"


def test_acr_images_diagnostics_blocked_when_target_env_prod() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(app_module.config, required_group_object_id="", auth_token="")

    with (
        patch.object(app_module, "config", patched_config),
        patch.dict(os.environ, {"TARGET_ENV": "prod"}, clear=False),
    ):
        response = client.get("/api/diagnostics/acr/images")

    body = response.json()
    assert response.status_code == 403
    assert "disabled" in body["error"].lower()
    assert body["target_env"] == "prod"


def test_acr_images_diagnostics_returns_tag_inventory_in_dev() -> None:
    client = TestClient(app_module.app)
    patched_config = replace(
        app_module.config,
        required_group_object_id="",
        auth_token="",
        ingestion_job_subscription_id="sub-123",
        ingestion_job_resource_group="rg-dev",
    )

    class _FakeCredential:
        def get_token(self, scope: str):
            assert scope == "https://management.azure.com/.default"
            return SimpleNamespace(token="fake-token")

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "value": [
                    {
                        "name": "20260417-1",
                        "digest": "sha256:abc",
                        "createdTime": "2026-04-17T12:00:00Z",
                        "lastUpdateTime": "2026-04-17T12:01:00Z",
                    },
                    {
                        "name": "20260417-2",
                        "digest": "sha256:abc",
                        "createdTime": "2026-04-17T12:05:00Z",
                        "lastUpdateTime": "2026-04-17T12:06:00Z",
                    },
                ]
            }

    def _fake_get(url: str, headers: dict[str, str], timeout: int):
        assert "acrdevaue04" in url
        assert "query-web" in url
        assert "n=5" in url
        assert headers["Authorization"] == "Bearer fake-token"
        assert timeout == 30
        return _FakeResponse()

    with (
        patch.object(app_module, "config", patched_config),
        patch.object(app_module, "credential", _FakeCredential()),
        patch.dict(os.environ, {"TARGET_ENV": "dev", "ACR_NAME": "acrdevaue04"}, clear=False),
        patch.object(app_module.requests, "get", side_effect=_fake_get),
    ):
        response = client.get(
            "/api/diagnostics/acr/images?repository=query-web&limit=5&expected_tag=20260417-2"
        )

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "acr-images-diagnostics"
    assert body["target_env"] == "dev"
    assert body["registry_name"] == "acrdevaue04"
    assert body["repository"] == "query-web"
    assert body["expected_tag"] == "20260417-2"
    assert body["tag_count"] == 2
    assert body["distinct_digest_count"] == 1
    assert body["quick_flags"]["repository_empty"] is False
    assert body["quick_flags"]["multiple_tags_share_digest"] is True
    assert body["quick_flags"]["expected_tag_present"] is True
    assert body["tags"][0]["name"] == "20260417-1"
