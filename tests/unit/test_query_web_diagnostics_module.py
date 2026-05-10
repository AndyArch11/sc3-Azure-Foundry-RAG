from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from query_web.endpoints.diagnostics import register_diagnostics_endpoints
from runtime.search import SearchClient


class _DefaultSearchClient:
    @property
    def index_name(self) -> str:
        return "grounding-index"

    def search(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        del query_text, top, vector_query, filters, select, extra_kwargs
        return []

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        del docs


def _build_client(
    *, search_client: SearchClient | None = None, storage_enabled: bool = True
) -> TestClient:
    app = FastAPI()
    config = SimpleNamespace(
        search_endpoint="https://example.search.windows.net",
        search_index_name="grounding-index",
        storage_account_name="stdev",
        storage_container_name="grounding-data",
        ingestion_job_name="ingestion-job-1",
    )
    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="fake-token"))
    register_diagnostics_endpoints(
        app,
        credential,
        config,
        search_client or _DefaultSearchClient(),
        lambda: storage_enabled,
        lambda: True,
        lambda: {"name": "job-1", "status": "Succeeded"},
        lambda prefix: {"would_delete": 0},
        lambda client, filter_expr: 0,
        lambda: "2026-04-17T00:00:00+00:00",
        {"corpus", "uploaded_at", "upload_batch"},
        SimpleNamespace(
            _is_authorised_request=lambda auth_token, request: True,
            _unauthorised_message=lambda request=None: "Unauthorised.",
        ),
    )
    return TestClient(app)


def test_indexer_history_diagnostics_returns_recent_errors() -> None:
    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_indexer_status(self, name: str):
            assert name == "grounding-index-indexer"
            history = [
                SimpleNamespace(
                    start_time="2026-04-17T01:00:00Z",
                    end_time="2026-04-17T01:01:00Z",
                    status="transientFailure",
                    items_processed=8,
                    items_failed=2,
                    errors=["boom-1", "boom-2"],
                    warnings=["warn-1"],
                )
            ]
            return SimpleNamespace(execution_history=history)

    with patch("azure.search.documents.indexes.SearchIndexerClient", _FakeSearchIndexerClient):
        client = _build_client()
        response = client.get("/api/diagnostics/search/indexer-history?limit=3")

    body = response.json()
    assert response.status_code == 200
    assert body["mode"] == "search-indexer-history-diagnostics"
    assert body["execution_history"][0]["items_failed"] == 2
    assert body["execution_history"][0]["errors_count"] == 2
    assert body["quick_flags"]["recent_errors"] is True


def test_indexer_history_diagnostics_separates_optional_warning_noise() -> None:
    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_indexer_status(self, name: str):
            assert name == "grounding-index-indexer"
            history = [
                SimpleNamespace(
                    start_time="2026-04-17T01:00:00Z",
                    end_time="2026-04-17T01:01:00Z",
                    status="success",
                    item_count=7,
                    failed_item_count=0,
                    errors=[],
                    warnings=[
                        {
                            "name": "Enrichment.ConditionalSkill.default-normalised-text-sha256",
                            "message": "Optional skill input is missing or empty",
                        }
                    ],
                )
            ]
            return SimpleNamespace(execution_history=history)

    with patch("azure.search.documents.indexes.SearchIndexerClient", _FakeSearchIndexerClient):
        client = _build_client()
        response = client.get("/api/diagnostics/search/indexer-history?limit=3")

    body = response.json()
    assert response.status_code == 200
    assert body["execution_history"][0]["warnings_count"] == 1
    assert body["execution_history"][0]["known_optional_warnings_count"] == 1
    assert body["execution_history"][0]["actionable_warnings_count"] == 0
    assert body["quick_flags"]["recent_warnings"] is False
    assert body["quick_flags"]["recent_known_optional_warnings"] is True


def test_index_samples_diagnostics_truncates_content_and_uses_selected_fields() -> None:
    class _FakeSearchClient:
        @property
        def index_name(self) -> str:
            return "grounding-index"

        def search(self, **kwargs):
            assert kwargs["top"] == 2
            assert kwargs["select"] == [
                "id",
                "source_name",
                "corpus",
                "corpus_role",
                "upload_batch",
                "uploaded_at",
            ]
            return [
                {
                    "id": "doc-1",
                    "source_name": "sample.pdf",
                    "corpus": "b",
                    "content": "x" * 700,
                }
            ]

        def load_documents(self, docs: list[dict[str, Any]]) -> None:
            del docs

    client = _build_client(search_client=_FakeSearchClient())
    response = client.get("/api/diagnostics/search/index-samples?limit=2&include_all_fields=false")

    body = response.json()
    assert response.status_code == 200
    assert body["documents_retrieved"] == 1
    assert body["documents"][0]["content"].endswith("...[truncated]")
    assert body["quick_flags"]["has_documents"] is True


def test_storage_metadata_validation_reports_missing_keys() -> None:
    class _FakeContainer:
        def list_blobs(self, name_starts_with=None):
            assert name_starts_with == "corpus-b/by-dedupe/"
            yield SimpleNamespace(
                name="corpus-b/by-dedupe/hash-1.pdf",
                metadata={"corpus": "b", "uploaded_at": "20260417T000000Z"},
            )
            yield SimpleNamespace(
                name="corpus-b/by-dedupe/hash-2.pdf",
                metadata={
                    "corpus": "b",
                    "uploaded_at": "20260417T000000Z",
                    "upload_batch": "batch-1",
                },
            )

    class _FakeBlobServiceClient:
        def __init__(self, account_url: str, credential: object):
            self.account_url = account_url
            self.credential = credential

        def get_container_client(self, container_name: str):
            assert container_name == "grounding-data"
            return _FakeContainer()

    with patch("azure.storage.blob.BlobServiceClient", _FakeBlobServiceClient):
        client = _build_client()
        response = client.get(
            "/api/diagnostics/storage/metadata-validation?prefix=corpus-b/by-dedupe/&sample_size=5"
        )

    body = response.json()
    assert response.status_code == 200
    assert body["configured"] is True
    assert body["total_scanned"] == 2
    assert body["blobs_with_complete_metadata"] == 1
    assert body["missing_metadata_distribution"]["upload_batch"] == 1
    assert body["quick_flags"]["critical_metadata_missing"] is True


def test_storage_metadata_validation_can_include_sample_metadata_values() -> None:
    class _FakeContainer:
        def list_blobs(self, name_starts_with=None):
            assert name_starts_with == "corpus-b/by-dedupe/"
            yield SimpleNamespace(
                name="corpus-b/by-dedupe/hash-1.pdf",
                metadata={
                    "corpus": "b",
                    "uploaded_at": "20260417T000000Z",
                    "upload_batch": "batch-123",
                },
            )

    class _FakeBlobServiceClient:
        def __init__(self, account_url: str, credential: object):
            self.account_url = account_url
            self.credential = credential

        def get_container_client(self, container_name: str):
            assert container_name == "grounding-data"
            return _FakeContainer()

    with patch("azure.storage.blob.BlobServiceClient", _FakeBlobServiceClient):
        client = _build_client()
        response = client.get(
            "/api/diagnostics/storage/metadata-validation"
            "?prefix=corpus-b/by-dedupe/&sample_size=5&include_values=true"
        )

    body = response.json()
    assert response.status_code == 200
    assert body["include_values"] is True
    assert body["sample_blobs"][0]["metadata_values"]["corpus"] == "b"
    assert body["sample_blobs"][0]["metadata_values"]["upload_batch"] == "batch-123"


def test_datasource_connectivity_diagnostics_enumerates_blobs() -> None:
    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_data_source_connection(self, name: str):
            assert name == "grounding-index-datasource"
            return SimpleNamespace(
                connection_string="BlobEndpoint=https://stdev.blob.core.windows.net/;AccountName=stdev;",
                container=SimpleNamespace(query="corpus-b/by-dedupe/"),
            )

    class _FakeContainer:
        def list_blobs(self, name_starts_with=None):
            assert name_starts_with == "corpus-b/by-dedupe/"
            for idx in range(3):
                yield SimpleNamespace(name=f"blob-{idx}")

    class _FakeBlobServiceClient:
        def __init__(self, account_url: str, credential: object):
            self.account_url = account_url
            self.credential = credential

        def get_container_client(self, container_name: str):
            assert container_name == "grounding-data"
            return _FakeContainer()

    with (
        patch("azure.search.documents.indexes.SearchIndexerClient", _FakeSearchIndexerClient),
        patch("azure.storage.blob.BlobServiceClient", _FakeBlobServiceClient),
        patch.dict(
            "os.environ",
            {"AZURE_SEARCH_DATASOURCE_NAME": "grounding-index-datasource"},
            clear=False,
        ),
    ):
        client = _build_client()
        response = client.get("/api/diagnostics/search/datasource-connectivity")

    body = response.json()
    assert response.status_code == 200
    assert body["datasource_name"] == "grounding-index-datasource"
    assert body["blob_enumeration_test"]["success"] is True
    assert body["blob_enumeration_test"]["blob_count"] == 3
    assert body["quick_flags"]["blobs_enumerable"] is True


def test_field_mappings_diagnostics_reports_missing_target_fields() -> None:
    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_indexer(self, name: str):
            assert name == "grounding-index-indexer"
            return SimpleNamespace(
                field_mappings=[
                    SimpleNamespace(
                        source_field_name="source_path", target_field_name="source_path"
                    ),
                    SimpleNamespace(
                        source_field_name="legacy_field", target_field_name="missing_field"
                    ),
                ]
            )

    class _FakeSearchIndexClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_index(self, name: str):
            assert name == "grounding-index"
            return SimpleNamespace(
                fields=[
                    SimpleNamespace(
                        name="source_path", type="Edm.String", searchable=True, filterable=False
                    ),
                    SimpleNamespace(
                        name="content", type="Edm.String", searchable=True, filterable=False
                    ),
                ]
            )

    with (
        patch("query_web.endpoints.diagnostics.SearchIndexClient", _FakeSearchIndexClient),
        patch("azure.search.documents.indexes.SearchIndexerClient", _FakeSearchIndexerClient),
    ):
        client = _build_client()
        response = client.get("/api/diagnostics/search/field-mappings")

    body = response.json()
    assert response.status_code == 200
    assert body["total_mappings"] == 2
    assert body["valid_mappings"] == 1
    assert body["validation_passed"] is False
    assert body["quick_flags"]["missing_fields"] is True


def test_ingestion_overview_hides_data_source_query_exception_details() -> None:
    class _FakeSearchIndexerClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_indexer_status(self, name: str):
            assert name == "grounding-index-indexer"
            return SimpleNamespace(execution_history=[])

        def get_data_source_connection(self, name: str):
            assert name == "grounding-index-datasource"
            raise ValueError("boom")

    with (
        patch("azure.search.documents.indexes.SearchIndexerClient", _FakeSearchIndexerClient),
        patch.dict(
            "os.environ",
            {"AZURE_SEARCH_DATASOURCE_NAME": "grounding-index-datasource"},
            clear=False,
        ),
    ):
        client = _build_client(storage_enabled=False)
        response = client.get("/api/diagnostics/ingestion/overview?include_blob_samples=false")

    body = response.json()
    assert response.status_code == 200
    assert body["scope_query_diagnostics"]["active_data_source_query_error"] == (
        "Internal server error; check logs for details."
    )
    assert "boom" not in response.text
