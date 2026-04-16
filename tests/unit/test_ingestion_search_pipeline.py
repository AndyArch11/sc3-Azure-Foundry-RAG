from __future__ import annotations

import json
from types import SimpleNamespace

from azure.core.credentials import AccessToken

from runtime.ingestion import search_pipeline
from runtime.ingestion.config import IngestionConfig


def _cfg() -> IngestionConfig:
    return IngestionConfig(
        search_endpoint="https://search.example",
        search_index_name="grounding-index",
        data_source_name="grounding-index-datasource",
        skillset_name="grounding-index-skillset",
        indexer_name="grounding-index-indexer",
        ai_services_endpoint="https://foundry.cognitiveservices.azure.com",
        azure_openai_endpoint="https://openai.example",
        embedding_deployment_name="text-embedding-3-large",
        embedding_dimensions=3072,
        storage_account_name="storacct",
        storage_container_name="grounding-data",
        storage_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Storage/storageAccounts/storacct",

        chunk_size=1000,
        chunk_overlap=100,
    )




class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        return AccessToken("token", 9999999999)




def test_delete_if_exists_swallows_not_found(monkeypatch) -> None:
    class _NotFound(Exception):
        pass

    called = {"n": 0}

    def _delete(name: str):
        called["n"] += 1
        raise _NotFound("missing")

    monkeypatch.setattr(search_pipeline, "ResourceNotFoundError", _NotFound)
    search_pipeline._delete_if_exists(_delete, "name", "kind")
    assert called["n"] == 1


def test_wait_for_indexer_success_and_timeout(monkeypatch) -> None:
    class _Run:
        def __init__(self, status: str):
            self.status = status
            self.item_count = 3
            self.failed_item_count = 1
            self.error_message = None
            self.errors = []
            self.warnings = []

    class _Status:
        def __init__(self, run):
            self.last_result = run

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            self.calls = 0

        def get_indexer_status(self, indexer_name: str):
            self.calls += 1
            return _Status(_Run("success"))

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)
    result = search_pipeline.wait_for_indexer(
        _cfg(), credential=_FakeCredential(), poll_interval_seconds=0, timeout_seconds=1
    )
    assert result["status"] == "success"
    assert result["items_processed"] == 3

    # timeout path
    class _NoResultClient:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def get_indexer_status(self, indexer_name: str):
            return _Status(None)

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _NoResultClient)
    result = search_pipeline.wait_for_indexer(
        _cfg(), credential=_FakeCredential(), poll_interval_seconds=0, timeout_seconds=0
    )
    assert result["status"] == "timeout"


def test_run_indexer_and_ensure_indexer(monkeypatch) -> None:
    events: list[str] = []

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def run_indexer(self, name: str):
            events.append(f"run:{name}")

        def create_or_update_indexer(self, indexer):
            events.append(f"ensure:{indexer.name}")

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)
    search_pipeline.run_indexer(_cfg(), credential=_FakeCredential())
    search_pipeline.ensure_indexer(_cfg(), credential=_FakeCredential())

    assert "run:grounding-index-indexer" in events
    assert "ensure:grounding-index-indexer" in events


def test_ensure_search_index_recreates_on_schema_conflict(monkeypatch) -> None:
    events: list[str] = []

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def create_or_update_index(self, index):
            events.append(f"create:{index.name}")
            if len(events) == 1:
                raise search_pipeline.HttpResponseError(
                    message="CannotChangeExistingField: Existing field 'id' cannot be changed"
                )

        def delete_index(self, name: str):
            events.append(f"delete_index:{name}")

    class _IndexerClient:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def delete_indexer(self, name: str):
            events.append(f"delete_indexer:{name}")

        def delete_skillset(self, name: str):
            events.append(f"delete_skillset:{name}")

    monkeypatch.setattr(search_pipeline, "SearchIndexClient", _Client)
    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _IndexerClient)

    search_pipeline.ensure_search_index(_cfg(), credential=_FakeCredential())

    assert events.count("create:grounding-index") == 2
    assert "delete_indexer:grounding-index-indexer" in events
    assert "delete_skillset:grounding-index-skillset" in events
    assert "delete_index:grounding-index" in events


def test_ensure_data_source_uses_storage_resource_id(monkeypatch) -> None:
    captured = {}

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def create_or_update_data_source_connection(self, ds):
            captured["name"] = ds.name
            captured["connection_string"] = ds.connection_string

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)

    search_pipeline.ensure_data_source(_cfg(), credential=_FakeCredential())

    assert captured["name"] == "grounding-index-datasource"
    assert captured["connection_string"].startswith("ResourceId=")


def test_ensure_skillset_uses_preview_rest_with_explicit_null_identity(monkeypatch) -> None:
    captured = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            captured["endpoint"] = endpoint

        def send_request(self, request):
            captured["method"] = request.method
            captured["url"] = request.url
            captured["body"] = json.loads(request.content)
            return _Response()

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)

    search_pipeline.ensure_skillset(_cfg(), credential=_FakeCredential())

    assert captured["method"] == "PUT"
    assert captured["url"].endswith(
        "/skillsets/grounding-index-skillset?api-version=2025-11-01-preview"
    )
    assert captured["body"]["cognitiveServices"]["@odata.type"] == (
        "#Microsoft.Azure.Search.AIServicesByIdentity"
    )
    assert captured["body"]["cognitiveServices"]["subdomainUrl"] == (
        "https://foundry.cognitiveservices.azure.com"
    )
    assert "identity" in captured["body"]["cognitiveServices"]
    assert captured["body"]["cognitiveServices"]["identity"] is None




def test_run_indexer_attaches_when_already_in_progress(monkeypatch) -> None:
    called = {"run": 0}

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def get_indexer_status(self, name: str):
            return SimpleNamespace(last_result=SimpleNamespace(status="inProgress"))

        def run_indexer(self, name: str):
            called["run"] += 1

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)

    search_pipeline.run_indexer(_cfg(), credential=_FakeCredential())

    assert called["run"] == 0


def test_run_indexer_handles_concurrent_resource_exists(monkeypatch) -> None:
    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def get_indexer_status(self, name: str):
            return SimpleNamespace(last_result=SimpleNamespace(status="success"))

        def run_indexer(self, name: str):
            raise search_pipeline.ResourceExistsError(message="already running")

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)

    # Should not raise when a concurrent invocation already started the run.
    search_pipeline.run_indexer(_cfg(), credential=_FakeCredential())


def test_wait_for_indexer_transient_failure_includes_errors_and_warnings(monkeypatch) -> None:
    class _Err:
        key = "doc1"
        name = "EnrichmentError"
        status_code = 500
        error_message = "skill failed"
        details = "details"
        documentation_link = "https://example/error"

    class _Warn:
        key = "doc2"
        name = "Warning"
        message = "minor issue"
        details = "warn-details"
        documentation_link = "https://example/warn"

    run = SimpleNamespace(
        status="transientFailure",
        item_count=10,
        failed_item_count=2,
        error_message=None,
        errors=[_Err()],
        warnings=[_Warn()],
    )

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def get_indexer_status(self, indexer_name: str):
            return SimpleNamespace(last_result=run)

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)

    result = search_pipeline.wait_for_indexer(
        _cfg(), credential=_FakeCredential(), poll_interval_seconds=0, timeout_seconds=1
    )

    assert result["status"] == "transientFailure"
    assert result["items_failed"] == 2
    assert result["error_message"] == "skill failed"
    assert len(result["errors"]) == 1
    assert len(result["warnings"]) == 1


def test_wait_for_indexer_handles_reset_then_success(monkeypatch) -> None:
    calls = {"n": 0}

    class _Client:
        def __init__(self, endpoint: str, credential) -> None:
            pass

        def get_indexer_status(self, indexer_name: str):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(last_result=SimpleNamespace(status="reset"))
            return SimpleNamespace(
                last_result=SimpleNamespace(
                    status="success",
                    item_count=1,
                    failed_item_count=0,
                    error_message=None,
                    errors=[],
                    warnings=[],
                )
            )

    monkeypatch.setattr(search_pipeline, "SearchIndexerClient", _Client)

    result = search_pipeline.wait_for_indexer(
        _cfg(), credential=_FakeCredential(), poll_interval_seconds=0, timeout_seconds=1
    )

    assert result["status"] == "success"
    assert calls["n"] >= 2
