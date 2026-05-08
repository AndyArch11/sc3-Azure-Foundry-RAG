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
    """Grounding index configuration for AWS OpenSearch."""

    opensearch_endpoint: str
    grounding_index_name: str

    @classmethod
    def from_env(cls) -> "AWSGroundingIndexConfig":
        """Build AWS grounding index config from environment variables."""
        endpoint = os.getenv("OPENSEARCH_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError("Required environment variable not set: OPENSEARCH_ENDPOINT")

        return cls(
            opensearch_endpoint=endpoint,
            grounding_index_name=(
                os.getenv("OPENSEARCH_GROUNDING_INDEX_NAME", "").strip() or "grounding-index"
            ),
        )


def _signed_headers(session: Any, method: str, url: str, body: str) -> dict[str, str]:
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
        logger.info("Grounding index already exists: %s", config.grounding_index_name)
        return
    if head_response.status_code != 404:
        head_response.raise_for_status()

    body = json.dumps(
        {
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 1,
                }
            },
            "mappings": {
                "properties": {
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
            },
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
