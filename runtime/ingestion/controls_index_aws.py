from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests


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
    head_response = requests.head(index_url, headers=head_headers, timeout=30)
    if head_response.status_code == 200:
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
                    "requirement_id": {"type": "keyword"},
                    "framework": {"type": "keyword"},
                    "framework_version": {"type": "keyword"},
                    "control_family": {"type": "keyword"},
                    "maturity_level": {"type": "integer"},
                    "requirement_text": {"type": "text"},
                    "guidance_text": {"type": "text"},
                    "keywords": {"type": "keyword"},
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
    put_response = requests.put(index_url, data=body, headers=put_headers, timeout=30)
    put_response.raise_for_status()
