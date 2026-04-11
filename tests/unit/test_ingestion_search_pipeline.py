from __future__ import annotations

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
        azure_openai_endpoint="https://openai.example",
        embedding_deployment_name="text-embedding-3-large",
        embedding_dimensions=3072,
        azure_openai_api_key=None,
        storage_account_name="storacct",
        storage_container_name="grounding-data",
        storage_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Storage/storageAccounts/storacct",
        cognitive_services_api_key=None,
        chunk_size=1000,
        chunk_overlap=100,
    )


def _cfg_with_cognitive_key() -> IngestionConfig:
    return IngestionConfig(
        search_endpoint="https://search.example",
        search_index_name="grounding-index",
        data_source_name="grounding-index-datasource",
        skillset_name="grounding-index-skillset",
        indexer_name="grounding-index-indexer",
        azure_openai_endpoint="https://openai.example",
        embedding_deployment_name="text-embedding-3-large",
        embedding_dimensions=3072,
        azure_openai_api_key="unused",
        storage_account_name="storacct",
        storage_container_name="grounding-data",
        storage_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Storage/storageAccounts/storacct",
        cognitive_services_api_key="cs-key",
        chunk_size=1000,
        chunk_overlap=100,
    )


class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        return AccessToken("token", 9999999999)


def test_cognitive_services_account_optional_key() -> None:
    cfg = _cfg()
    assert search_pipeline._cognitive_services_account(cfg) is None
    assert search_pipeline._cognitive_services_account(_cfg_with_cognitive_key()) is not None


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
