from __future__ import annotations

import pytest

from runtime.ingestion.config import IngestionConfig, _require


def test_require_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with pytest.raises(ValueError, match="Required environment variable not set: MISSING_KEY"):
        _require("MISSING_KEY")


def test_ingestion_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://search.example")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://openai.example")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "storacct")
    monkeypatch.setenv(
        "AZURE_STORAGE_RESOURCE_ID",
        "/subscriptions/x/resourceGroups/y/providers/Microsoft.Storage/storageAccounts/storacct",
    )
    monkeypatch.setenv(
        "AZURE_ENRICHMENT_MI_RESOURCE_ID",
        "/subscriptions/x/resourceGroups/y/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-agent-runtime",
    )
    monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "grounding-index")
    monkeypatch.setenv("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "3072")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER_NAME", "grounding-data")
    monkeypatch.setenv("CHUNK_SIZE", "1000")
    monkeypatch.setenv("CHUNK_OVERLAP", "100")

    cfg = IngestionConfig.from_env()

    assert cfg.search_endpoint == "https://search.example"
    assert cfg.search_index_name == "grounding-index"
    assert cfg.data_source_name == "grounding-index-datasource"
    assert cfg.skillset_name == "grounding-index-skillset"
    assert cfg.indexer_name == "grounding-index-indexer"
    assert cfg.embedding_deployment_name == "text-embedding-3-large"
    assert cfg.embedding_dimensions == 3072
    assert cfg.storage_account_name == "storacct"
    assert cfg.storage_container_name == "grounding-data"
    assert (
        cfg.enrichment_mi_resource_id
        == "/subscriptions/x/resourceGroups/y/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-agent-runtime"
    )
    assert cfg.chunk_size == 1000
    assert cfg.chunk_overlap == 100
