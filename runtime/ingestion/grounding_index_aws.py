"""
grounding_index_aws

Grounding index management for AWS OpenSearch.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

try:
    from runtime.outbound_instrumentation import request_with_instrumentation
except ModuleNotFoundError:
    from outbound_instrumentation import request_with_instrumentation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AWSGroundingIndexConfig:
    """Grounding index configuration for AWS OpenSearch.

    The KNN setting is part of the index definition and must be chosen when the
    index is created. Changing it later requires deleting and recreating the
    index.

    Attributes:
        opensearch_endpoint: The endpoint URL for the OpenSearch cluster.
        grounding_index_name: The name of the grounding index.
        knn_enabled: Whether KNN (k-nearest neighbors) is enabled for the index.
        embedding_dimensions: The number of dimensions for embeddings if KNN is enabled.
    """

    opensearch_endpoint: str
    grounding_index_name: str
    knn_enabled: bool = False
    embedding_dimensions: int = 1024

    @classmethod
    def from_env(cls) -> "AWSGroundingIndexConfig":
        """Build AWS grounding index config from environment variables.

        Returns:
            An instance of AWSGroundingIndexConfig populated with values from environment variables.
        """
        endpoint = os.getenv("OPENSEARCH_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError("Required environment variable not set: OPENSEARCH_ENDPOINT")

        return cls(
            opensearch_endpoint=endpoint,
            grounding_index_name=(
                os.getenv("OPENSEARCH_GROUNDING_INDEX_NAME", "").strip() or "grounding-index"
            ),
            knn_enabled=_truthy_env("OPENSEARCH_GROUNDING_INDEX_KNN_ENABLED", default="false"),
            embedding_dimensions=_int_env(
                "OPENSEARCH_GROUNDING_EMBEDDING_DIMENSIONS",
                _int_env("BEDROCK_EMBEDDING_DIMENSIONS", 1024),
            ),
        )


def _truthy_env(name: str, default: str = "false") -> bool:
    """Check if an environment variable is truthy.

    Args:
        name: The name of the environment variable.
        default: The default value if the environment variable is not set.

    Returns:
        True if the environment variable is truthy, False otherwise.
    """
    raw = os.getenv(name, default).strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    """Get an integer value from an environment variable.

    Args:
        name: The name of the environment variable.
        default: The default value if the environment variable is not set.

    Returns:
        The integer value of the environment variable.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"Environment variable {name} must be > 0")
    return value


def _signed_headers(session: Any, method: str, url: str, body: str) -> dict[str, str]:
    """Generate signed headers for AWS OpenSearch requests.

    Args:
        session: The boto3 session object.
        method: The HTTP method (e.g., "GET", "POST").
        url: The full URL of the request.
        body: The request body as a string.

    Returns:
        A dictionary of signed headers for the request.
    """
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("Unable to resolve AWS credentials for OpenSearch request signing")

    frozen_credentials = credentials.get_frozen_credentials()
    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(
        frozen_credentials,
        "es",
        session.region_name or os.getenv("AWS_REGION", "us-east-1"),
    ).add_auth(request)
    return dict(request.headers.items())


def ensure_grounding_index_aws(config: AWSGroundingIndexConfig, session: Any) -> None:
    """Ensure grounding index exists in OpenSearch with required mappings.

    Uses text-only mappings (no dense vectors); hybrid search on AWS uses BM25 only.
    Field set mirrors what _hybrid_search() selects in query_web/pipeline/search.py.

    Args:
        config: The AWSGroundingIndexConfig instance containing OpenSearch configuration.
        session: The boto3 session object for signing requests.
    Raises:
        RuntimeError: If the existing index mapping is incompatible with KNN settings.
    """
    index_url = f"{config.opensearch_endpoint.rstrip('/')}/{config.grounding_index_name}"

    head_headers = _signed_headers(session, "HEAD", index_url, "")
    head_response = request_with_instrumentation(
        "HEAD",
        index_url,
        logger=logger,
        headers=head_headers,
        timeout=30,
        system="aws-opensearch",
        operation="grounding_index_exists",
        request_callable=requests.head,
    )
    if head_response.status_code == 200:
        if config.knn_enabled:
            mapping_url = f"{index_url}/_mapping"
            mapping_headers = _signed_headers(session, "GET", mapping_url, "")
            mapping_response = request_with_instrumentation(
                "GET",
                mapping_url,
                logger=logger,
                headers=mapping_headers,
                timeout=30,
                system="aws-opensearch",
                operation="grounding_index_mapping",
                request_callable=requests.get,
            )
            mapping_response.raise_for_status()

            try:
                mapping_payload = mapping_response.json()
            except ValueError:
                mapping_payload = {}
            index_mapping = (
                mapping_payload.get(config.grounding_index_name, {})
                if isinstance(mapping_payload, dict)
                else {}
            )
            properties = (
                index_mapping.get("mappings", {}).get("properties", {})
                if isinstance(index_mapping, dict)
                else {}
            )
            embedding = properties.get("embedding", {}) if isinstance(properties, dict) else {}
            embedding_type = embedding.get("type") if isinstance(embedding, dict) else None
            embedding_dimension = (
                embedding.get("dimension") if isinstance(embedding, dict) else None
            )

            if embedding_type != "knn_vector" or int(embedding_dimension or 0) != int(
                config.embedding_dimensions
            ):
                raise RuntimeError(
                    "Existing grounding index mapping is incompatible with KNN settings "
                    f"(embedding.type={embedding_type!r}, embedding.dimension={embedding_dimension!r}, "
                    f"expected type='knn_vector', dimension={config.embedding_dimensions}). "
                    "Delete and recreate the index, then re-ingest documents."
                )
        logger.info("Grounding index already exists: %s", config.grounding_index_name)
        return
    if head_response.status_code != 404:
        head_response.raise_for_status()

    index_settings: dict[str, Any] = {
        "number_of_shards": 1,
        "number_of_replicas": 1,
    }
    if config.knn_enabled:
        index_settings["knn"] = True

    properties: dict[str, Any] = {
        # Primary searchable text field.
        "content": {"type": "text", "analyzer": "english"},
        # Chunk provenance.
        "chunk_id": {"type": "keyword"},
        "chunk_index": {"type": "integer"},
        "source_path": {"type": "keyword"},
        "source_name": {"type": "keyword"},
        "source_type": {"type": "keyword"},
        # Corpus metadata (mirrors Azure blob metadata_* projection).
        "corpus": {"type": "keyword"},
        "corpus_role": {"type": "keyword"},
        "upload_source": {"type": "keyword"},
        "uploaded_by": {"type": "keyword"},
        "upload_batch": {"type": "keyword"},
        "uploaded_at": {"type": "keyword"},
        "original_filename": {"type": "keyword"},
        # Dedupe hashes — useful for filtering/re-indexing.
        "content_sha256": {"type": "keyword"},
        "normalised_text_sha256": {"type": "keyword"},
        "dedupe_hash": {"type": "keyword"},
        "dedupe_method": {"type": "keyword"},
        # Internal ingestion timestamp.
        "ingested_at": {"type": "date"},
    }
    if config.knn_enabled:
        properties["embedding"] = {
            "type": "knn_vector",
            "dimension": int(config.embedding_dimensions),
        }

    body = json.dumps(
        {
            "settings": {"index": index_settings},
            "mappings": {"properties": properties},
        },
        ensure_ascii=True,
    )

    put_headers = _signed_headers(session, "PUT", index_url, body)
    put_response = request_with_instrumentation(
        "PUT",
        index_url,
        logger=logger,
        data=body,
        headers=put_headers,
        timeout=30,
        system="aws-opensearch",
        operation="create_grounding_index",
        request_callable=requests.put,
    )
    put_response.raise_for_status()
    logger.info("Grounding index created: %s", config.grounding_index_name)
