"""
AWS reset mode: remove indexed data and optional S3 source objects on AWS.

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
class AWSResetConfig:
    """Configuration for AWS reset mode.

    Attributes:
        opensearch_endpoint: The endpoint URL for the OpenSearch cluster.
        opensearch_index_name: The name of the OpenSearch index to reset.
        s3_bucket_name: The name of the S3 bucket containing source objects.
        s3_prefix: An optional prefix for S3 objects to delete.
    """

    opensearch_endpoint: str
    opensearch_index_name: str
    s3_bucket_name: str
    s3_prefix: str | None

    @classmethod
    def from_env(cls) -> "AWSResetConfig":
        """Build AWS reset configuration from environment variables.

        Args:
            cls: The class itself.
        Returns:
            An instance of AWSResetConfig populated with values from environment variables."""
        endpoint = os.getenv("OPENSEARCH_ENDPOINT", "").strip() or ""
        index_name = (
            os.getenv("OPENSEARCH_INDEX", "").strip()
            or os.getenv("OPENSEARCH_INDEX_NAME", "").strip()
            or "grounding-index"
        )
        bucket_name = (
            os.getenv("AWS_S3_BUCKET_NAME", "").strip()
            or os.getenv("S3_BUCKET_NAME", "").strip()
            or ""
        )
        if not endpoint:
            raise ValueError("Required environment variable not set: OPENSEARCH_ENDPOINT")
        if not bucket_name:
            raise ValueError("Required environment variable not set: AWS_S3_BUCKET_NAME")

        prefix = os.getenv("AWS_S3_PREFIX", "").strip() or None
        return cls(
            opensearch_endpoint=endpoint,
            opensearch_index_name=index_name,
            s3_bucket_name=bucket_name,
            s3_prefix=prefix,
        )


def _signed_headers(session: Any, method: str, url: str, body: str) -> dict[str, str]:
    """Generate AWS SigV4 signed headers for an OpenSearch request.

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


def _delete_index_documents(config: AWSResetConfig, session: Any) -> int:
    """Delete all documents in the specified OpenSearch index.

    Args:
        config: The AWSResetConfig object containing OpenSearch configuration.
        session: The boto3 session object.

    Returns:
        The number of documents deleted.
    """
    url = (
        f"{config.opensearch_endpoint.rstrip('/')}/"
        f"{config.opensearch_index_name}/_delete_by_query?conflicts=proceed&refresh=true"
    )
    body = json.dumps({"query": {"match_all": {}}}, ensure_ascii=True)
    headers = _signed_headers(session, "POST", url, body)
    response = request_with_instrumentation(
        "POST",
        url,
        logger=logger,
        data=body,
        headers=headers,
        timeout=30,
        system="aws-opensearch",
        operation="delete_by_query",
        request_callable=requests.post,
    )

    if response.status_code == 404:
        return 0

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        details = response.text[:500]
        raise RuntimeError(f"Failed to purge OpenSearch index documents: {details}") from exc

    payload = response.json()
    return int(payload.get("deleted", 0) or 0)


def _purge_s3_objects(config: AWSResetConfig, storage_client: Any) -> int:
    """Delete all objects in the specified S3 bucket and prefix.

    Args:
        config: The AWSResetConfig object containing S3 configuration.
        storage_client: The S3 client object.

    Returns:
        The number of objects deleted.
    """
    keys = storage_client.list_objects(config.s3_bucket_name, prefix=config.s3_prefix)
    deleted = 0
    for key in keys:
        storage_client.delete_object(config.s3_bucket_name, key)
        deleted += 1
    return deleted


def reset_loaded_data_aws(
    config: AWSResetConfig,
    session: Any,
    storage_client: Any,
    *,
    purge_objects: bool = False,
) -> dict[str, Any]:
    """Remove indexed data and optional S3 source objects on AWS.

    Args:
        config: The AWSResetConfig object containing OpenSearch and S3 configuration.
        session: The boto3 session object for signing requests.
        storage_client: The S3 client object for deleting objects.
        purge_objects: If True, delete S3 source objects under the specified prefix.
    Returns:
        A dictionary containing counts of deleted index documents and source objects.
    """
    deleted_docs = _delete_index_documents(config, session)
    deleted_objects = 0

    if purge_objects:
        deleted_objects = _purge_s3_objects(config, storage_client)

    return {
        "deleted_index_documents": deleted_docs,
        "indexer_reset": False,
        "deleted_source_objects": deleted_objects,
        "storage_bucket": config.s3_bucket_name,
        "search_index": config.opensearch_index_name,
    }
