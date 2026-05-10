from __future__ import annotations

import os
from dataclasses import dataclass

from azure.core.credentials import TokenCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchField,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
)


@dataclass(frozen=True)
class ControlsIndexConfig:
    """ControlsIndexConfig."""

    search_endpoint: str
    controls_index_name: str

    @classmethod
    def from_env(cls) -> "ControlsIndexConfig":
        """Run from env."""
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
    """Create or update the dedicated controls index used for requirement records."""
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

    index = SearchIndex(
        name=config.controls_index_name,
        fields=fields,
        semantic_search=semantic_search,
    )

    client.create_or_update_index(index)
