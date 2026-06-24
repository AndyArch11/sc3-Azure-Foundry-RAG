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


def _desired_embedding_dimension() -> int:
    return int(os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024").strip() or "1024")


def _existing_index_is_compatible(config: AWSControlsIndexConfig, session: Any) -> bool:
    index_url = f"{config.opensearch_endpoint.rstrip('/')}/{config.controls_index_name}"
    headers = _signed_headers(session, "GET", index_url, "")
    response = request_with_instrumentation(
        "GET",
        index_url,
        logger=logger,
        headers=headers,
        timeout=30,
        system="aws-opensearch",
        operation="describe_index",
        request_callable=requests.get,
    )
    response.raise_for_status()
    payload = response.json()
    index_state = payload.get(config.controls_index_name, {}) if isinstance(payload, dict) else {}

    mappings = index_state.get("mappings", {}) if isinstance(index_state, dict) else {}
    properties = mappings.get("properties", {}) if isinstance(mappings, dict) else {}
    embedding = properties.get("embedding", {}) if isinstance(properties, dict) else {}

    settings = index_state.get("settings", {}) if isinstance(index_state, dict) else {}
    index_settings = settings.get("index", {}) if isinstance(settings, dict) else {}
    knn_enabled = str(index_settings.get("knn", "false")).strip().lower() == "true"

    return (
        knn_enabled
        and isinstance(embedding, dict)
        and embedding.get("type") == "knn_vector"
        and int(embedding.get("dimension") or 0) == _desired_embedding_dimension()
    )


def _delete_controls_index_aws(config: AWSControlsIndexConfig, session: Any) -> None:
    index_url = f"{config.opensearch_endpoint.rstrip('/')}/{config.controls_index_name}"
    headers = _signed_headers(session, "DELETE", index_url, "")
    response = request_with_instrumentation(
        "DELETE",
        index_url,
        logger=logger,
        headers=headers,
        timeout=30,
        system="aws-opensearch",
        operation="delete_index",
        request_callable=requests.delete,
    )
    if response.status_code == 404:
        return
    response.raise_for_status()


@dataclass(frozen=True)
class AWSControlsIndexConfig:
    """Controls index configuration for AWS OpenSearch."""

    opensearch_endpoint: str
    controls_index_name: str

    @classmethod
    def from_env(cls) -> "AWSControlsIndexConfig":
        """Build AWS controls index config from environment variables."""
        endpoint = os.getenv("OPENSEARCH_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError("Required environment variable not set: OPENSEARCH_ENDPOINT")

        return cls(
            opensearch_endpoint=endpoint,
            controls_index_name=(
                os.getenv("OPENSEARCH_CONTROLS_INDEX_NAME", "").strip() or "controls-index"
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


def ensure_controls_index_aws(config: AWSControlsIndexConfig, session: Any) -> None:
    """Ensure controls index exists in OpenSearch with required mappings."""
    index_url = f"{config.opensearch_endpoint.rstrip('/')}/{config.controls_index_name}"

    head_headers = _signed_headers(session, "HEAD", index_url, "")
    head_response = request_with_instrumentation(
        "HEAD",
        index_url,
        logger=logger,
        headers=head_headers,
        timeout=30,
        system="aws-opensearch",
        operation="index_exists",
        request_callable=requests.head,
    )
    if head_response.status_code == 200:
        if _existing_index_is_compatible(config, session):
            return

        logger.warning(
            "Controls index schema is incompatible with the expected vector mapping; recreating index %s",
            config.controls_index_name,
        )
        _delete_controls_index_aws(config, session)
    else:
        if head_response.status_code != 404:
            head_response.raise_for_status()

    body = json.dumps(
        {
            "settings": {
                "index": {
                    "knn": True,
                    "number_of_shards": 1,
                    "number_of_replicas": 1,
                }
            },
            "mappings": {
                "properties": {
                    "requirement_id": {"type": "keyword"},
                    "framework": {"type": "keyword"},
                    "framework_version": {"type": "keyword"},
                    "control_family": {"type": "keyword"},
                    "maturity_level": {"type": "integer"},
                    "requirement_text": {"type": "text"},
                    "guidance_text": {"type": "text"},
                    "keywords": {"type": "keyword"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": _desired_embedding_dimension(),
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                        },
                    },
                    "source_uri": {"type": "keyword"},
                    "source_section": {"type": "keyword"},
                    "effective_date": {"type": "keyword"},
                    "jurisdiction_or_scope": {"type": "keyword"},
                    "ingestion_manifest_hash": {"type": "keyword"},
                    "ingestion_loaded_at": {"type": "date"},
                    "control_applicability_scope": {"type": "keyword"},
                    "applicability_confidence": {"type": "float"},
                    "applicability_uncertain": {"type": "boolean"},
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
        operation="create_index",
        request_callable=requests.put,
    )
    put_response.raise_for_status()
