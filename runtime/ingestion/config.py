from __future__ import annotations

import os
from dataclasses import dataclass


def _require(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        raise ValueError(f"Required environment variable not set: {key}")
    return v


@dataclass(frozen=True)
class IngestionConfig:
    # Azure AI Search
    search_endpoint: str
    search_index_name: str
    data_source_name: str
    skillset_name: str
    indexer_name: str

    # Azure AI Services (Foundry) — used for skillset enrichment billing via managed identity
    ai_services_endpoint: str

    # Azure OpenAI embedding skill
    azure_openai_endpoint: str
    embedding_deployment_name: str
    embedding_dimensions: int

    # Azure Storage (blob source)
    storage_account_name: str
    storage_container_name: str
    # ARM resource ID for managed-identity data source connection, e.g.:
    #   /subscriptions/.../resourceGroups/.../providers/Microsoft.Storage/storageAccounts/name
    # Obtain with: az storage account show -g <rg> -n <name> --query id -o tsv
    storage_resource_id: str

    # Chunking
    chunk_size: int
    chunk_overlap: int

    @classmethod
    def from_env(cls) -> IngestionConfig:
        index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "grounding-index")
        return cls(
            search_endpoint=_require("AZURE_SEARCH_ENDPOINT"),
            search_index_name=index_name,
            data_source_name=os.environ.get(
                "AZURE_SEARCH_DATASOURCE_NAME", f"{index_name}-datasource"
            ),
            skillset_name=os.environ.get("AZURE_SEARCH_SKILLSET_NAME", f"{index_name}-skillset"),
            indexer_name=os.environ.get("AZURE_SEARCH_INDEXER_NAME", f"{index_name}-indexer"),
            ai_services_endpoint=_require("AI_SERVICES_ENDPOINT"),
            azure_openai_endpoint=_require("AZURE_OPENAI_ENDPOINT"),
            embedding_deployment_name=os.environ.get(
                "EMBEDDING_DEPLOYMENT_NAME", "text-embedding-ada-002"
            ),
            embedding_dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS", "1536")),
            storage_account_name=_require("AZURE_STORAGE_ACCOUNT_NAME"),
            storage_container_name=os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "grounding-data"),
            storage_resource_id=_require("AZURE_STORAGE_RESOURCE_ID"),
            chunk_size=int(os.environ.get("CHUNK_SIZE", "1200")),
            chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", "200")),
        )
