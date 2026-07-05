from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import requests

try:
    from runtime.outbound_instrumentation import request_with_instrumentation
except ModuleNotFoundError:
    from outbound_instrumentation import request_with_instrumentation

from .grounding_index_aws import AWSGroundingIndexConfig

logger = logging.getLogger(__name__)

_BULK_BATCH_SIZE = 100


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


def _bulk_index_chunks(
    config: AWSGroundingIndexConfig,
    session: Any,
    chunks: list[dict[str, Any]],
) -> tuple[int, int]:
    """Bulk-index a batch of chunk documents. Returns (indexed, failed).

    Args:
        config: The AWSGroundingIndexConfig object containing OpenSearch configuration.
        session: The boto3 session object.
        chunks: A list of chunk documents to index.
    Returns:
        A tuple containing the number of successfully indexed documents and the number of failed documents.
    """
    if not chunks:
        return 0, 0

    bulk_url = f"{config.opensearch_endpoint.rstrip('/')}/_bulk?refresh=true"
    lines: list[str] = []
    for chunk in chunks:
        doc_id = str(chunk.get("chunk_id") or "").strip()
        if not doc_id:
            continue
        lines.append(
            json.dumps(
                {"index": {"_index": config.grounding_index_name, "_id": doc_id}},
                ensure_ascii=True,
            )
        )
        lines.append(json.dumps(chunk, ensure_ascii=True))

    if not lines:
        return 0, 0

    body = "\n".join(lines) + "\n"
    headers = _signed_headers(session, "POST", bulk_url, body)
    response = request_with_instrumentation(
        "POST",
        bulk_url,
        logger=logger,
        data=body,
        headers=headers,
        timeout=60,
        system="aws-opensearch",
        operation="bulk_index_grounding_chunks",
        request_callable=requests.post,
    )
    response.raise_for_status()

    payload = response.json()
    indexed = 0
    failed = 0
    for item in payload.get("items", []):
        op = item.get("index") or {}
        status = op.get("status", 0)
        if 200 <= status < 300:
            indexed += 1
        else:
            failed += 1
            err = op.get("error", {})
            logger.warning(
                "Grounding chunk index failure: id=%s type=%s reason=%s",
                op.get("_id"),
                err.get("type"),
                err.get("reason"),
            )
    return indexed, failed


def upload_grounding_chunks_aws(
    config: AWSGroundingIndexConfig,
    session: Any,
    chunks: list[dict[str, Any]],
    *,
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Upload grounding chunk records to OpenSearch in batches.

    Each entry in ``chunks`` must have at least ``chunk_id`` and ``content`` keys plus
    the corpus/provenance metadata fields expected by _hybrid_search().

    When ``replace_existing`` is True, existing documents with matching chunk_ids are
    overwritten (index operation is idempotent by default in OpenSearch).

    Args:
        config: The AWSGroundingIndexConfig object containing OpenSearch configuration.
        session: The boto3 session object.
        chunks: A list of chunk documents to index.
        replace_existing: If True, existing documents with matching chunk_ids will be replaced.

    Returns:
        A dictionary containing counts of records indexed, skipped, and failed.
    """
    if not replace_existing:
        # Check if the index already has documents from the same source_path / dedupe_hash
        # to avoid re-indexing already-indexed content. Use dedupe_hash if available.
        existing_dedupe_hashes = _fetch_existing_dedupe_hashes(config, session, chunks)
    else:
        existing_dedupe_hashes = set()

    to_index: list[dict[str, Any]] = []
    skipped = 0
    now_str = datetime.now(UTC).isoformat()

    for chunk in chunks:
        dedupe_hash = str(chunk.get("dedupe_hash") or "").strip()
        if dedupe_hash and dedupe_hash in existing_dedupe_hashes and not replace_existing:
            skipped += 1
            continue
        doc = dict(chunk)
        doc.setdefault("ingested_at", now_str)
        to_index.append(doc)

    total_indexed = 0
    total_failed = 0
    for batch_start in range(0, len(to_index), _BULK_BATCH_SIZE):
        batch = to_index[batch_start : batch_start + _BULK_BATCH_SIZE]
        indexed, failed = _bulk_index_chunks(config, session, batch)
        total_indexed += indexed
        total_failed += failed

    logger.info(
        "Grounding upload complete: %d indexed, %d skipped (existing), %d failed",
        total_indexed,
        skipped,
        total_failed,
    )
    return {
        "records_indexed": total_indexed,
        "records_skipped": skipped,
        "records_failed": total_failed,
    }


def _fetch_existing_dedupe_hashes(
    config: AWSGroundingIndexConfig,
    session: Any,
    chunks: list[dict[str, Any]],
) -> set[str]:
    """Return the set of dedupe_hash values already present in the grounding index.

    Args:
        config: The AWSGroundingIndexConfig object containing OpenSearch configuration.
        session: The boto3 session object.
        chunks: A list of chunk documents to index.

    Returns:
        A set of dedupe_hash values already present in the grounding index.
    """
    candidate_hashes = {
        str(c.get("dedupe_hash") or "").strip()
        for c in chunks
        if str(c.get("dedupe_hash") or "").strip()
    }
    if not candidate_hashes:
        return set()

    search_url = f"{config.opensearch_endpoint.rstrip('/')}/{config.grounding_index_name}/_search"
    body_payload = {
        "size": 0,
        "aggs": {
            "existing_hashes": {
                "terms": {
                    "field": "dedupe_hash",
                    "size": len(candidate_hashes) + 10,
                    "include": list(candidate_hashes),
                }
            }
        },
    }
    body = json.dumps(body_payload, ensure_ascii=True)
    headers = _signed_headers(session, "POST", search_url, body)
    response = request_with_instrumentation(
        "POST",
        search_url,
        logger=logger,
        data=body,
        headers=headers,
        timeout=30,
        system="aws-opensearch",
        operation="check_existing_grounding_hashes",
        request_callable=requests.post,
    )
    if response.status_code == 404:
        return set()
    response.raise_for_status()

    payload = response.json()
    buckets = payload.get("aggregations", {}).get("existing_hashes", {}).get("buckets", [])
    return {str(b.get("key", "")) for b in buckets if b.get("doc_count", 0) > 0}
