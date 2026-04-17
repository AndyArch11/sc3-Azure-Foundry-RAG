"""
Azure AI Search indexer pipeline manager.

Provisions and runs a server-side enrichment pipeline using Azure AI Search
built-in cognitive skills.  The pipeline replaces client-side document parsing
with server-side processing, which:

  - Handles more formats natively (PDF, DOCX, XLSX, PPTX, HTML and more).
  - Applies OcrSkill for scanned / image-only PDFs.
  - Uses DocumentExtractionSkill for uniform extraction control across formats.
  - Applies MergeSkill to combine native text with OCR text.
  - Uses SplitSkill for server-side chunking (configurable size / overlap).
  - Generates vector embeddings via AzureOpenAIEmbeddingSkill.
  - Projects one Search document per chunk via index projections.

Skill execution flow (per blob document):

  /document/file_data
      └─► DocumentExtractionSkill
              ├─► /document/extracted_content      (native text)
              └─► /document/normalised_images/*    (page images from PDF / raster)
                      └─► OcrSkill
                              └─► /document/normalised_images/*/ocr_text  (OCR text)

  MergeSkill (extracted_content + ocr_text items)
      └─► /document/merged_content

  SplitSkill (merged_content)
      └─► /document/pages/*                        (one item per chunk)

  AzureOpenAIEmbeddingSkill (context: /document/pages/*)
      └─► /document/pages/*/content_vector         (embedding per chunk)

  Index projections (source context: /document/pages/*)
      └─► One Search document per chunk with: content, content_vector,
          source_path, source_name, parent_id

Prerequisites
-------------
- Search service system-assigned managed identity must be enabled.
- That identity requires:
    Storage Blob Data Reader    on the storage account
    Cognitive Services User     on the Foundry / AI Services account (for OcrSkill)
    Cognitive Services OpenAI User  on the Foundry account (for embedding skill)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, cast

from azure.core.credentials import TokenCredential
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    BlobIndexerImageAction,
    ConditionalSkill,
    DocumentExtractionSkill,
    HnswAlgorithmConfiguration,
    IndexingParameters,
    IndexingParametersConfiguration,
    IndexProjectionMode,
    InputFieldMappingEntry,
    LexicalAnalyzerName,
    MergeSkill,
    OcrSkill,
    OutputFieldMappingEntry,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerDataSourceType,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerSkillset,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SplitSkill,
    VectorSearch,
    VectorSearchProfile,
)

from .config import IngestionConfig

logger = logging.getLogger(__name__)


def _create_or_update_skillset_via_preview_rest(
    client: SearchIndexerClient,
    config: IngestionConfig,
    skillset: SearchIndexerSkillset,
) -> None:
    """Persist skillset with preview contract details required for AIServicesByIdentity.

    The Search preview API expects `cognitiveServices.identity` to be explicitly null
    when using the search service system-assigned managed identity. The SDK model omits
    null-valued properties, so we submit this payload as raw JSON bytes via the low-level
    generated operation to preserve null fields.
    """

    payload = skillset.serialize()
    payload["cognitiveServices"] = {
        "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
        "description": "Bill enrichment against the attached AI Services account via the search service managed identity.",
        "subdomainUrl": config.ai_services_endpoint,
        "identity": None,
    }

    logger.warning(
        "Skillset PUT payload — projection sources: %s",
        {
            m["name"]: m["source"]
            for m in payload.get("indexProjections", {})
            .get("selectors", [{}])[0]
            .get("mappings", [])
            if m.get("name")
            in (
                "uploaded_by",
                "uploaded_at",
                "content_sha256",
                "normalised_text_sha256",
            )
        },
    )

    # send_request is not exposed on this SDK client shape; use generated operation.
    result = cast(Any, client)._client.skillsets.create_or_update(
        skillset_name=config.skillset_name,
        prefer="return=representation",
        skillset=json.dumps(payload).encode("utf-8"),
    )
    result_skills = [
        s.get("name") if isinstance(s, dict) else getattr(s, "name", None)
        for s in (getattr(result, "skills", []) or [])
    ]
    logger.warning("Skillset PUT response — skills: %s", result_skills)


def _delete_if_exists(delete_fn, resource_name: str, resource_kind: str) -> None:
    try:
        delete_fn(resource_name)
        logger.warning("Deleted Search %s for schema reset: %s", resource_kind, resource_name)
    except ResourceNotFoundError:
        return


def _reset_search_artifacts_for_schema_change(
    config: IngestionConfig,
    credential: TokenCredential,
) -> None:
    indexer_client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)
    index_client = SearchIndexClient(endpoint=config.search_endpoint, credential=credential)

    _delete_if_exists(indexer_client.delete_indexer, config.indexer_name, "indexer")
    _delete_if_exists(indexer_client.delete_skillset, config.skillset_name, "skillset")
    _delete_if_exists(index_client.delete_index, config.search_index_name, "index")


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def ensure_search_index(config: IngestionConfig, credential: TokenCredential) -> None:
    """Create or update the target Search index schema."""
    client = SearchIndexClient(endpoint=config.search_endpoint, credential=credential)

    fields = [
        # Chunk-level key generated by index projection.
        SearchField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            searchable=True,
            analyzer_name=LexicalAnalyzerName.KEYWORD,
            filterable=True,
        ),
        # Parent document identifier; used for selective deletion on re-index.
        SimpleField(
            name="parent_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        # Chunk text — primary searchable content.
        SearchField(
            name="content",
            type=SearchFieldDataType.String,
            searchable=True,
            analyzer_name="en.microsoft",
        ),
        # Dense vector for semantic / hybrid retrieval.
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=config.embedding_dimensions,
            vector_search_profile_name="hnsw-profile",
        ),
        # Blob storage path (full URL).
        SimpleField(
            name="source_path",
            type=SearchFieldDataType.String,
            filterable=True,
            retrievable=True,
        ),
        # Original filename.
        SimpleField(
            name="source_name",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        # Corpus and ingestion metadata projected from blob metadata_* fields.
        SimpleField(
            name="corpus",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="corpus_role",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="upload_source",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="uploaded_by",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="upload_batch",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="uploaded_at",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="original_filename",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="content_sha256",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="normalised_text_sha256",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="dedupe_hash",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
        SimpleField(
            name="dedupe_method",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
            retrievable=True,
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
        profiles=[
            VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-config")
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="default-semantic",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="content")]
                ),
            )
        ]
    )

    index = SearchIndex(
        name=config.search_index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    try:
        client.create_or_update_index(index)
    except HttpResponseError as exc:
        error_text = str(exc)
        if (
            "CannotChangeExistingField" not in error_text
            and "Existing field 'id' cannot be changed" not in error_text
        ):
            raise

        logger.warning(
            "Search index schema is incompatible with the existing '%s' definition; recreating index-dependent artifacts.",
            config.search_index_name,
        )
        _reset_search_artifacts_for_schema_change(config, credential)
        client.create_or_update_index(index)

    logger.info("Search index ensured: %s", config.search_index_name)


# ---------------------------------------------------------------------------
# Data source
# ---------------------------------------------------------------------------


def ensure_data_source(config: IngestionConfig, credential: TokenCredential) -> None:
    """Create or update the blob storage data source using managed identity auth."""
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)

    # Managed-identity connection string — no credentials stored in code.
    # Requires Search service system-assigned MI to have Storage Blob Data Reader
    # role on the storage account.
    connection_string = f"ResourceId={config.storage_resource_id}"

    data_source = SearchIndexerDataSourceConnection(
        name=config.data_source_name,
        type=SearchIndexerDataSourceType.AZURE_BLOB,
        connection_string=connection_string,
        container=SearchIndexerDataContainer(
            name=config.storage_container_name,
            query=config.storage_container_query,
        ),
    )

    client.create_or_update_data_source_connection(data_source)
    logger.info("Data source ensured: %s", config.data_source_name)


# ---------------------------------------------------------------------------
# Skillset
# ---------------------------------------------------------------------------




def ensure_skillset(config: IngestionConfig, credential: TokenCredential) -> None:
    """
    Create or update the enrichment skillset.

    Skills applied in order:
      1. DocumentExtractionSkill  — extract text and normalised images from raw file data
      2. OcrSkill                 — OCR text from normalised images (scanned / image PDFs)
      3. MergeSkill               — merge native text with OCR text
      4. SplitSkill               — chunk merged text into pages of configurable size
      5. AzureOpenAIEmbeddingSkill — embed each chunk using the configured deployment
    """
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)

    # 1. DocumentExtractionSkill ─────────────────────────────────────────────
    # Extracts text content and normalised page images from the raw file blob.
    # Requires indexer param allow_skillset_to_read_file_data = True.
    # Supports: PDF, DOCX, XLSX, PPTX, HTML and other common formats.
    document_extraction = DocumentExtractionSkill(
        name="document-extraction",
        description="Extract text and images from the source document",
        inputs=[InputFieldMappingEntry(name="file_data", source="/document/file_data")],
        outputs=[
            OutputFieldMappingEntry(name="content", target_name="extracted_content"),
            OutputFieldMappingEntry(name="normalized_images", target_name="normalised_images"),
        ],
        parsing_mode="default",
        data_to_extract="contentAndMetadata",
        configuration={
            "imageAction": "generateNormalizedImages",
            "normalizedImageMaxWidth": 2000,
            "normalizedImageMaxHeight": 2000,
        },
    )

    # 2. OcrSkill ────────────────────────────────────────────────────────────
    # Runs OCR on each normalised page image.  Handles scanned PDFs and any
    # document page that is stored as a raster image rather than text.
    ocr = OcrSkill(
        name="ocr",
        description="OCR text from normalised page images (scanned / image PDFs)",
        context="/document/normalised_images/*",
        inputs=[InputFieldMappingEntry(name="image", source="/document/normalised_images/*")],
        outputs=[OutputFieldMappingEntry(name="text", target_name="ocr_text")],
        default_language_code="en",
    )

    # 3. MergeSkill ──────────────────────────────────────────────────────────
    # Merges the natively extracted text with the OCR text from images so that
    # the downstream Split and Embed skills see a single unified text body.
    merge = MergeSkill(
        name="merge-text",
        description="Merge native extracted text with OCR text",
        context="/document",
        inputs=[
            InputFieldMappingEntry(name="text", source="/document/extracted_content"),
            InputFieldMappingEntry(
                name="itemsToInsert",
                source="/document/normalised_images/*/ocr_text",
            ),
            InputFieldMappingEntry(
                name="offsets",
                source="/document/normalised_images/*/contentOffset",
            ),
        ],
        outputs=[OutputFieldMappingEntry(name="mergedText", target_name="merged_content")],
        insert_pre_tag="",
        insert_post_tag=" ",
    )

    # 4. SplitSkill ──────────────────────────────────────────────────────────
    # Splits the merged text into overlapping pages (chunks).
    # maximum_page_length and page_overlap_length mirror the local chunking
    # strategy in chunking.py so results are comparable between modes.
    split = SplitSkill(
        name="split",
        description="Split merged text into overlapping chunks",
        context="/document",
        text_split_mode="pages",
        maximum_page_length=config.chunk_size,
        page_overlap_length=config.chunk_overlap,
        inputs=[InputFieldMappingEntry(name="text", source="/document/merged_content")],
        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
    )

    # 5. ConditionalSkill — default uploaded_by ────────────────────────────
    # Blobs that were uploaded before the uploaded_by metadata tag was
    # introduced (or via out-of-band tooling) may lack that tag.  Rather
    # than letting the index-projection fail on a missing field we coerce
    # the value to an empty string so every chunk document is always
    # written with a valid uploaded_by field.
    default_uploaded_by = ConditionalSkill(
        name="default-uploaded-by",
        description="Default uploaded_by to empty string when blob metadata is absent",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_uploaded_by) == null || $(/document/metadata_uploaded_by) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='unknown'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_uploaded_by",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="uploaded_by_safe",
            )
        ],
    )

    # 6. ConditionalSkill - default uploaded_at
    # Some legacy blobs do not include uploaded_at metadata. Coerce missing
    # values to an empty string so index projections do not fail.
    default_uploaded_at = ConditionalSkill(
        name="default-uploaded-at",
        description="Default uploaded_at to empty string when blob metadata is absent",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_uploaded_at) == null || $(/document/metadata_uploaded_at) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='1970-01-01T00:00:00Z'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_uploaded_at",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="uploaded_at_safe",
            )
        ],
    )

    # 7. ConditionalSkill - default normalised_text_sha256
    # Legacy blobs can carry an empty normalised_text_sha256 value. Fall back
    # to dedupe_hash so projection fields remain non-empty and indexable.
    default_dedupe_hash = ConditionalSkill(
        name="default-dedupe-hash",
        description="Default dedupe_hash to storage path when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_dedupe_hash) == null || $(/document/metadata_dedupe_hash) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="/document/metadata_storage_path"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_dedupe_hash",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="dedupe_hash_safe",
            )
        ],
    )

    default_dedupe_method = ConditionalSkill(
        name="default-dedupe-method",
        description="Default dedupe_method when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_dedupe_method) == null || $(/document/metadata_dedupe_method) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='content_sha256'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_dedupe_method",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="dedupe_method_safe",
            )
        ],
    )

    default_corpus = ConditionalSkill(
        name="default-corpus",
        description="Default corpus when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_corpus) == null || $(/document/metadata_corpus) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='legacy'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_corpus",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="corpus_safe",
            )
        ],
    )

    default_corpus_role = ConditionalSkill(
        name="default-corpus-role",
        description="Default corpus_role when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_corpus_role) == null || $(/document/metadata_corpus_role) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='unknown'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_corpus_role",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="corpus_role_safe",
            )
        ],
    )

    default_upload_source = ConditionalSkill(
        name="default-upload-source",
        description="Default upload_source when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_upload_source) == null || $(/document/metadata_upload_source) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='legacy'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_upload_source",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="upload_source_safe",
            )
        ],
    )

    default_upload_batch = ConditionalSkill(
        name="default-upload-batch",
        description="Default upload_batch when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_upload_batch) == null || $(/document/metadata_upload_batch) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="='legacy'"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_upload_batch",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="upload_batch_safe",
            )
        ],
    )

    default_original_filename = ConditionalSkill(
        name="default-original-filename",
        description="Default original_filename to storage name when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_original_filename) == null || $(/document/metadata_original_filename) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="/document/metadata_storage_name"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_original_filename",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="original_filename_safe",
            )
        ],
    )

    default_normalised_text_sha256 = ConditionalSkill(
        name="default-normalised-text-sha256",
        description="Default normalised_text_sha256 to dedupe_hash when metadata is empty",
        context="/document",
        inputs=[
            InputFieldMappingEntry(
                name="condition",
                source="=$(/document/metadata_normalised_text_sha256) == null || $(/document/metadata_normalised_text_sha256) == ''",
            ),
            InputFieldMappingEntry(name="whenTrue", source="/document/dedupe_hash_safe"),
            InputFieldMappingEntry(
                name="whenFalse",
                source="/document/metadata_normalised_text_sha256",
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="output",
                target_name="normalised_text_sha256_safe",
            )
        ],
    )

    # 8. AzureOpenAIEmbeddingSkill ───────────────────────────────────────────
    # Generates a dense vector embedding for each chunk.
    # Context is per-page so one embedding is produced per chunk.
    # auth: the search service system-assigned managed identity is used;
    # it must have Cognitive Services OpenAI User on the Foundry / AI Services account.
    embedding = AzureOpenAIEmbeddingSkill(
        name="embedding",
        description="Embed each text chunk using Azure OpenAI",
        context="/document/pages/*",
        resource_url=config.azure_openai_endpoint,
        deployment_name=config.embedding_deployment_name,
        model_name=config.embedding_deployment_name,
        dimensions=config.embedding_dimensions,
        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
        outputs=[OutputFieldMappingEntry(name="embedding", target_name="content_vector")],
    )

    # Index projections ──────────────────────────────────────────────────────
    # Project each chunk as a separate Search document.
    # The search service generates a stable chunk-level key (GENERATED_KEY_AS_ID)
    # making re-indexing idempotent.
    index_projections = SearchIndexerIndexProjection(
        selectors=[
            SearchIndexerIndexProjectionSelector(
                target_index_name=config.search_index_name,
                parent_key_field_name="parent_id",
                source_context="/document/pages/*",
                mappings=[
                    InputFieldMappingEntry(name="content", source="/document/pages/*"),
                    InputFieldMappingEntry(
                        name="content_vector",
                        source="/document/pages/*/content_vector",
                    ),
                    InputFieldMappingEntry(
                        name="source_path",
                        source="/document/metadata_storage_path",
                    ),
                    InputFieldMappingEntry(
                        name="source_name",
                        source="/document/metadata_storage_name",
                    ),
                    InputFieldMappingEntry(
                        name="corpus",
                        source="/document/corpus_safe",
                    ),
                    InputFieldMappingEntry(
                        name="corpus_role",
                        source="/document/corpus_role_safe",
                    ),
                    InputFieldMappingEntry(
                        name="upload_source",
                        source="/document/upload_source_safe",
                    ),
                    InputFieldMappingEntry(
                        name="uploaded_by",
                        source="/document/uploaded_by_safe",
                    ),
                    InputFieldMappingEntry(
                        name="upload_batch",
                        source="/document/upload_batch_safe",
                    ),
                    InputFieldMappingEntry(
                        name="uploaded_at",
                        source="/document/uploaded_at_safe",
                    ),
                    InputFieldMappingEntry(
                        name="original_filename",
                        source="/document/original_filename_safe",
                    ),
                    InputFieldMappingEntry(
                        name="content_sha256",
                        source="/document/dedupe_hash_safe",
                    ),
                    InputFieldMappingEntry(
                        name="normalised_text_sha256",
                        source="/document/normalised_text_sha256_safe",
                    ),
                    InputFieldMappingEntry(
                        name="dedupe_hash",
                        source="/document/dedupe_hash_safe",
                    ),
                    InputFieldMappingEntry(
                        name="dedupe_method",
                        source="/document/dedupe_method_safe",
                    ),
                ],
            )
        ],
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
        ),
    )


    # Use the search service's system-assigned managed identity to bill enrichment
    # against the attached AI Services account via RBAC (no key required).
    skillset = SearchIndexerSkillset(
        name=config.skillset_name,
        description="PDF and Excel enrichment: extract → OCR → merge → split → embed",
        skills=[
            document_extraction,
            ocr,
            merge,
            split,
            default_uploaded_by,
            default_uploaded_at,
            default_dedupe_hash,
            default_dedupe_method,
            default_corpus,
            default_corpus_role,
            default_upload_source,
            default_upload_batch,
            default_original_filename,
            default_normalised_text_sha256,
            embedding,
        ],
        index_projection=index_projections,
    )

    _create_or_update_skillset_via_preview_rest(client, config, skillset)
    logger.info("Skillset ensured: %s", config.skillset_name)


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------


def ensure_indexer(config: IngestionConfig, credential: TokenCredential) -> None:
    """Create or update the blob indexer binding the pipeline together."""
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)

    parameters = IndexingParameters(
        configuration=IndexingParametersConfiguration(
            # Required for DocumentExtractionSkill to receive raw file bytes.
            allow_skillset_to_read_file_data=True,
            # Generate normalised page images so OcrSkill can process them.
            image_action=BlobIndexerImageAction.GENERATE_NORMALIZED_IMAGES,
            # azure-search-documents 11.6.0 injects queryTimeout by default,
            # but Azure Blob indexers reject that property.
            query_timeout=cast(Any, None),
        )
    )

    indexer = SearchIndexer(
        name=config.indexer_name,
        description="Blob indexer for PDF and Excel source documents",
        data_source_name=config.data_source_name,
        target_index_name=config.search_index_name,
        skillset_name=config.skillset_name,
        parameters=parameters,
    )

    client.create_or_update_indexer(indexer)
    logger.info("Indexer ensured: %s", config.indexer_name)


# ---------------------------------------------------------------------------
# Run and monitor
# ---------------------------------------------------------------------------


def _is_indexer_run_in_progress(status: Any) -> bool:
    """Best-effort check for active indexer execution across SDK status shapes.

    Notes:
    - last_result.status is IndexerExecutionStatus: inProgress | success | transientFailure | reset
    - status.status is top-level IndexerStatus: running | error | unknown
      TOP-LEVEL "running" means the indexer is healthy/operational, NOT that an execution
      is in flight.  Only last_result.status == "inprogress" reliably signals active execution.
    """
    try:
        last_result = getattr(status, "last_result", None)
        if last_result is not None:
            last_status = str(getattr(last_result, "status", "")).strip().lower()
            if last_status == "inprogress":
                return True
    except Exception:
        pass

    return False


def run_indexer(config: IngestionConfig, credential: TokenCredential) -> None:
    """Trigger an indexer run, or attach when another run is already active."""
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)

    # If another worker already started the indexer, attach to that run.
    try:
        status = client.get_indexer_status(config.indexer_name)
        if _is_indexer_run_in_progress(status):
            logger.warning(
                "Indexer %s is already running; attaching to active run",
                config.indexer_name,
            )
            return
    except (ResourceNotFoundError, AttributeError):
        pass

    try:
        client.run_indexer(config.indexer_name)
        logger.info("Indexer run triggered: %s", config.indexer_name)
    except ResourceExistsError:
        # 409 from concurrent trigger race: treat as attached run, not failure.
        logger.warning(
            "Indexer %s invocation already in progress (409); attaching to active run",
            config.indexer_name,
        )


def wait_for_indexer(
    config: IngestionConfig,
    credential: TokenCredential,
    poll_interval_seconds: int = 10,
    timeout_seconds: int = 1800,
) -> dict:
    """
    Poll until the indexer run completes (success or error) and return a
    summary dict with keys: status, items_processed, items_failed, error_message.
    """
    client = SearchIndexerClient(endpoint=config.search_endpoint, credential=credential)
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            status = client.get_indexer_status(config.indexer_name)
        except ResourceNotFoundError:
            logger.warning("Indexer status not yet available, retrying…")
            time.sleep(poll_interval_seconds)
            continue

        run = status.last_result
        if run is None:
            time.sleep(poll_interval_seconds)
            continue

        status_text = str(run.status or "").strip()
        status_lower = status_text.lower()

        if status_lower in ("success", "transientfailure"):
            errors = [
                {
                    "key": error.key,
                    "name": error.name,
                    "status_code": error.status_code,
                    "error_message": error.error_message,
                    "details": error.details,
                    "documentation_link": error.documentation_link,
                }
                for error in (run.errors or [])
            ]
            warnings = [
                {
                    "key": warning.key,
                    "name": warning.name,
                    "message": warning.message,
                    "details": warning.details,
                    "documentation_link": warning.documentation_link,
                }
                for warning in (run.warnings or [])
            ]

            if errors:
                logger.error("Indexer reported %d item-level error(s): %s", len(errors), errors)
            if warnings:
                optional_conditional_warnings = [
                    warning
                    for warning in warnings
                    if str(warning.get("name") or "").startswith("Enrichment.ConditionalSkill.default-")
                    and "Optional skill input is missing or empty"
                    in str(warning.get("message") or "")
                ]
                actionable_warnings = [
                    warning for warning in warnings if warning not in optional_conditional_warnings
                ]

                optional_counts: dict[str, int] = {}
                for warning in optional_conditional_warnings:
                    name = str(warning.get("name") or "(unknown)")
                    optional_counts[name] = optional_counts.get(name, 0) + 1

                if optional_conditional_warnings:
                    logger.info(
                        "Indexer reported %d known optional metadata warning(s) (suppressed details): %s",
                        len(optional_conditional_warnings),
                        optional_counts,
                    )

                if actionable_warnings:
                    actionable_counts: dict[str, int] = {}
                    for warning in actionable_warnings:
                        name = str(warning.get("name") or "(unknown)")
                        actionable_counts[name] = actionable_counts.get(name, 0) + 1
                    logger.warning(
                        "Indexer reported %d actionable warning(s) grouped by skill/input: %s",
                        len(actionable_warnings),
                        actionable_counts,
                    )

            return {
                "status": status_text,
                "items_processed": run.item_count,
                "items_failed": run.failed_item_count,
                "error_message": run.error_message
                or (errors[0]["error_message"] if errors else None),
                "errors": errors,
                "warnings": warnings,
            }

        if status_lower == "reset":
            logger.info("Indexer state is reset; waiting for active run result…")
            time.sleep(poll_interval_seconds)
            continue

        if status_lower == "inprogress":
            logger.info(
                "Indexer running… status=%s items=%s failed=%s",
                run.status,
                run.item_count,
                run.failed_item_count,
            )
            time.sleep(poll_interval_seconds)
            continue

        logger.warning(
            "Unexpected indexer execution status '%s'; waiting for terminal status",
            status_text or "(empty)",
        )
        time.sleep(poll_interval_seconds)
        continue

    return {
        "status": "timeout",
        "items_processed": None,
        "items_failed": None,
        "error_message": f"Timed out waiting for indexer after {timeout_seconds}s",
    }
