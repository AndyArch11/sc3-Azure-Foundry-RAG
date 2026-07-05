"""
Controls index configuration for Azure Cognitive Search.
This module provides the ControlsIndexConfig dataclass and the ensure_controls_index function to manage the dedicated controls index used for requirement records in Azure Cognitive Search.
The ControlsIndexConfig dataclass encapsulates the configuration parameters required to connect to the Azure Cognitive Search service and specify the controls index name.
The ensure_controls_index function creates or updates the controls index with the necessary fields, semantic search configuration, and vector search configuration to support requirement records ingestion and retrieval.
The controls index is designed to store requirement records with various attributes, including requirement ID, framework, framework version, control family, maturity level, requirement text, guidance text, keywords,
content vector, source URI, source section, effective date, jurisdiction or scope, ingestion manifest hash, ingestion loaded at timestamp, control applicability scope, applicability confidence, and applicability uncertainty.
The index is configured to support semantic search and vector search capabilities, enabling efficient retrieval of requirement records based on content and embeddings.
The ensure_controls_index function checks for the existence of the controls index and creates or updates it as needed, ensuring that the index is compatible with the expected mappings and settings.
It leverages the Azure SDK for Python, specifically the azure-search-documents package, to interact with the Azure Cognitive Search service and manage the controls index.

"""

from __future__ import annotations

import os
from dataclasses import dataclass

from azure.core.credentials import TokenCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)


@dataclass(frozen=True)
class ControlsIndexConfig:
    """ControlsIndexConfig.

    Attributes:
        search_endpoint: The endpoint URL for the Azure Cognitive Search service.
        controls_index_name: The name of the dedicated controls index used for requirement records.
    """

    search_endpoint: str
    controls_index_name: str

    @classmethod
    def from_env(cls) -> "ControlsIndexConfig":
        """Build ControlsIndexConfig from environment variables.

        Raises:
            ValueError: If any required environment variable is missing.
        """
        search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
        if not search_endpoint:
            raise ValueError("Required environment variable not set: AZURE_SEARCH_ENDPOINT")

        return cls(
            search_endpoint=search_endpoint,
            controls_index_name=os.environ.get(
                "AZURE_SEARCH_CONTROLS_INDEX_NAME", "controls-index"
            ),
        )


def ensure_controls_index(config: ControlsIndexConfig, credential: TokenCredential) -> None:
    """Create or update the dedicated controls index used for requirement records.

    Args:
        config: The ControlsIndexConfig instance containing Azure Cognitive Search configuration.
        credential: The TokenCredential instance used for authenticating with Azure Cognitive Search.
    """
    client = SearchIndexClient(endpoint=config.search_endpoint, credential=credential)

    fields = [
        SimpleField(
            name="requirement_id",
            type="Edm.String",
            key=True,
            filterable=True,
            sortable=True,
            retrievable=True,
        ),
        SimpleField(
            name="framework",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="framework_version",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="control_family",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="maturity_level",
            type="Edm.Int32",
            filterable=True,
            facetable=True,
            sortable=True,
            retrievable=True,
        ),
        SearchField(
            name="requirement_text",
            type="Edm.String",
            searchable=True,
            analyzer_name="en.microsoft",
            retrievable=True,
        ),
        SearchField(
            name="guidance_text",
            type="Edm.String",
            searchable=True,
            analyzer_name="en.microsoft",
            retrievable=True,
        ),
        SearchField(
            name="keywords",
            type="Collection(Edm.String)",
            searchable=True,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SearchField(
            name="content_vector",
            type="Collection(Edm.Single)",
            searchable=True,
            vector_search_dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS", "1536")),
            vector_search_profile_name="controls-hnsw-profile",
            retrievable=False,
        ),
        SimpleField(
            name="source_uri",
            type="Edm.String",
            filterable=True,
            retrievable=True,
        ),
        SimpleField(
            name="source_section",
            type="Edm.String",
            filterable=True,
            retrievable=True,
        ),
        SimpleField(
            name="effective_date",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="jurisdiction_or_scope",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="ingestion_manifest_hash",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="ingestion_loaded_at",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="control_applicability_scope",
            type="Edm.String",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="applicability_confidence",
            type="Edm.Double",
            filterable=True,
            sortable=True,
            retrievable=True,
        ),
        SimpleField(
            name="applicability_uncertain",
            type="Edm.Boolean",
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
    ]

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="controls-semantic",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[
                        SemanticField(field_name="requirement_text"),
                        SemanticField(field_name="guidance_text"),
                    ],
                    keywords_fields=[SemanticField(field_name="keywords")],
                ),
            )
        ]
    )

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="controls-hnsw-config")],
        profiles=[
            VectorSearchProfile(
                name="controls-hnsw-profile",
                algorithm_configuration_name="controls-hnsw-config",
            )
        ],
    )

    index = SearchIndex(
        name=config.controls_index_name,
        fields=fields,
        semantic_search=semantic_search,
        vector_search=vector_search,
    )

    client.create_or_update_index(index)
