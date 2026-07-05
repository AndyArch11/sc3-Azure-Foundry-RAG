"""
AWS Controls Index Management

This module provides functionality for managing the controls index in AWS OpenSearch, including ensuring the index exists with the required mappings and settings, checking for compatibility of existing indices, and deleting incompatible indices.
It defines the `AWSControlsIndexConfig` dataclass for configuration, and functions to ensure the controls index is present and correctly configured, as well as to delete the index if necessary.
The module uses AWS request signing to authenticate requests to the OpenSearch service, and includes error handling and logging to facilitate debugging and monitoring of index management operations.
The controls index is used to store control-related data, including embeddings, and is configured to support k-NN vector search for efficient retrieval of control information based on vector similarity.
The module is designed to be used in the context of an ingestion pipeline, where control data is ingested and indexed in OpenSearch for subsequent retrieval and analysis.
It provides a robust and flexible approach to managing the controls index, ensuring that it is always in a compatible state for use with the ingestion pipeline and associated applications.
The module is intended to be used in conjunction with other components of the ingestion pipeline, such as data ingestion, processing, and analysis, to facilitate the management of control data in a structured and efficient manner.
It is implemented using standard Python libraries and the requests library for HTTP communication, and leverages AWS SDK components for request signing and authentication with the OpenSearch service.
The module is designed to be easily configurable through environment variables, allowing for seamless integration into different deployment environments and workflows.
It provides clear and informative logging messages to assist with monitoring and troubleshooting of index management operations, and includes error handling to ensure that any issues encountered during index management are reported and addressed appropriately.
The module is intended for use by developers and operators responsible for managing the ingestion pipeline and associated control data, providing them with the tools and functionality needed to ensure that the controls index is always in a compatible and operational state for use with the ingestion pipeline and associated applications.
It is designed to be extensible and adaptable to different use cases and requirements, allowing for customisation of index settings and mappings as needed to support specific control data management workflows and requirements.
The module is part of a larger ingestion framework that supports the ingestion, processing, and analysis of control data, providing a comprehensive solution for managing control information in a structured and efficient manner.

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


def _desired_embedding_dimension() -> int:
    """Run desired embedding dimension.

    Returns:
        The desired embedding dimension as an integer.
    """
    return int(os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024").strip() or "1024")


def _existing_index_is_compatible(config: AWSControlsIndexConfig, session: Any) -> bool:
    """Check if the existing controls index is compatible with the expected mappings and settings.

    Args:
        config: The AWSControlsIndexConfig instance containing index configuration.
        session: The AWS session object used for signing requests.

    Returns:
        True if the existing index is compatible, False otherwise.
    """
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
    """Delete the controls index in AWS OpenSearch.

    Args:
        config: The AWSControlsIndexConfig instance containing index configuration.
        session: The AWS session object used for signing requests.
    """
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
    """Controls index configuration for AWS OpenSearch.

    Attributes:
        opensearch_endpoint: The endpoint URL for the AWS OpenSearch service.
        controls_index_name: The name of the controls index in OpenSearch.
    """

    opensearch_endpoint: str
    controls_index_name: str

    @classmethod
    def from_env(cls) -> "AWSControlsIndexConfig":
        """Build AWS controls index config from environment variables.

        args:
            cls: The AWSControlsIndexConfig class.
        Raises:
            ValueError: If any required environment variable is missing.
        Returns:
            An instance of AWSControlsIndexConfig populated with values from environment variables.
        """
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
    """Generate signed headers for AWS OpenSearch requests using SigV4Auth.

    Args:
        session: The AWS session object used for signing requests.
        method: The HTTP method for the request (e.g., "GET", "POST").
        url: The URL of the OpenSearch endpoint.
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


def ensure_controls_index_aws(config: AWSControlsIndexConfig, session: Any) -> None:
    """Ensure controls index exists in OpenSearch with required mappings.

    Args:
        config: The AWSControlsIndexConfig instance containing OpenSearch configuration.
        session: The AWS session object used for signing requests.
    """
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
