"""
ingestion publish controls to AWS OpenSearch

"""

from __future__ import annotations

import hashlib
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

from .controls_index_aws import AWSControlsIndexConfig

logger = logging.getLogger(__name__)


def _controls_embedding_enabled() -> bool:
    """Check if controls embedding is enabled via environment variable.
    Returns:
        True if embedding is enabled, False otherwise.
    """
    raw = os.getenv("CONTROLS_EMBED_ON_PUBLISH", "false").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _controls_embedding_text(record: dict[str, Any]) -> str:
    """Generate embedding text for a control record.
    Args:
        record: A dictionary representing a control record.
    Returns:
        A string containing the combined text for embedding, truncated to 6000 characters.
    """
    requirement_text = str(record.get("requirement_text") or "").strip()
    guidance_text = str(record.get("guidance_text") or "").strip()
    keywords = record.get("keywords") or []
    keyword_text = " ".join(str(k).strip() for k in keywords if str(k).strip())

    combined = "\n".join(
        part for part in [requirement_text, guidance_text, keyword_text] if part
    ).strip()
    return combined[:6000]


def _controls_manifest_hash(records: list[dict[str, Any]]) -> str:
    """Compute a manifest hash for a list of control records.
    Args:
        records: A list of dictionaries representing control records.
    Returns:
        A SHA-256 hash of the canonical JSON representation of the records.
    """
    canonical_rows: list[dict[str, Any]] = []
    for record in records:
        canonical_rows.append(
            {
                "requirement_id": record.get("requirement_id", ""),
                "framework": record.get("framework", ""),
                "framework_version": record.get("framework_version", ""),
                "control_family": record.get("control_family", ""),
                "maturity_level": record.get("maturity_level"),
                "requirement_text": record.get("requirement_text", ""),
                "guidance_text": record.get("guidance_text", ""),
                "keywords": sorted(record.get("keywords", []) or []),
                "source_uri": record.get("source_uri", ""),
                "source_section": record.get("source_section", ""),
                "effective_date": record.get("effective_date", ""),
                "jurisdiction_or_scope": record.get("jurisdiction_or_scope", ""),
            }
        )

    canonical_rows.sort(key=lambda row: str(row["requirement_id"]))
    payload = json.dumps(canonical_rows, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _embed_text_aws(text: str, session: Any) -> list[float] | None:
    """Generate an embedding vector for the given text using AWS Bedrock.
    Args:
        text: The input text to embed.
        session: The boto3 session object for signing requests.
    Returns:
        A list of floats representing the embedding vector, or None if embedding is not available.
    """
    model_id = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "").strip()
    if not model_id:
        return None

    bedrock = session.client("bedrock-runtime")
    payload = {"inputText": text}
    response = bedrock.invoke_model(modelId=model_id, body=json.dumps(payload).encode("utf-8"))
    body_stream = response.get("body")
    if body_stream is None:
        raise RuntimeError("Bedrock embedding response body was empty")
    payload_obj = json.loads(body_stream.read())

    vector = payload_obj.get("embedding")
    if vector is None:
        by_type = payload_obj.get("embeddingsByType")
        if isinstance(by_type, dict):
            float_vectors = by_type.get("float")
            if isinstance(float_vectors, list) and float_vectors:
                vector = float_vectors[0]
    if not isinstance(vector, list) or not vector:
        raise RuntimeError("Bedrock embedding response did not include a vector")
    return [float(v) for v in vector]


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


def _search_existing_framework_version(
    config: AWSControlsIndexConfig,
    session: Any,
    framework: str,
    framework_version: str,
) -> tuple[list[str], set[str]]:
    """Search for existing framework versions in AWS OpenSearch.

    Args:
        config: The AWSControlsIndexConfig object containing OpenSearch configuration.
        session: The boto3 session object.
        framework: The name of the framework to search for.
        framework_version: The version of the framework to search for.

    Returns:
        A tuple containing a list of requirement IDs and a set of ingestion manifest hashes.
    """
    search_url = f"{config.opensearch_endpoint.rstrip('/')}/{config.controls_index_name}/_search"
    body_payload = {
        "size": 1000,
        "_source": ["requirement_id", "ingestion_manifest_hash"],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"framework.keyword": framework}},
                    {"term": {"framework_version.keyword": framework_version}},
                ]
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
        operation="search_existing_framework_version",
        request_callable=requests.post,
    )
    if response.status_code == 404:
        return [], set()
    response.raise_for_status()

    payload = response.json()
    hits = payload.get("hits", {}).get("hits", [])

    requirement_ids: list[str] = []
    manifest_hashes: set[str] = set()
    for hit in hits:
        source = hit.get("_source", {}) if isinstance(hit, dict) else {}
        req_id = str(source.get("requirement_id", "")).strip()
        if req_id:
            requirement_ids.append(req_id)
        manifest = str(source.get("ingestion_manifest_hash", "")).strip()
        if manifest:
            manifest_hashes.add(manifest)

    return requirement_ids, manifest_hashes


def _bulk_delete_requirements(
    config: AWSControlsIndexConfig,
    session: Any,
    requirement_ids: list[str],
) -> None:
    """Bulk delete requirements from AWS OpenSearch.
    Args:
        config: The AWSControlsIndexConfig object containing OpenSearch configuration.
        session: The boto3 session object.
        requirement_ids: A list of requirement IDs to delete.
    """
    if not requirement_ids:
        return

    bulk_url = f"{config.opensearch_endpoint.rstrip('/')}/_bulk?refresh=true"
    lines: list[str] = []
    for req_id in requirement_ids:
        lines.append(
            json.dumps(
                {"delete": {"_index": config.controls_index_name, "_id": req_id}},
                ensure_ascii=True,
            )
        )

    body = "\n".join(lines) + "\n"
    headers = _signed_headers(session, "POST", bulk_url, body)
    response = request_with_instrumentation(
        "POST",
        bulk_url,
        logger=logger,
        data=body,
        headers=headers,
        timeout=30,
        system="aws-opensearch",
        operation="bulk_delete_requirements",
        request_callable=requests.post,
    )
    response.raise_for_status()


def upload_controls_records_aws(
    config: AWSControlsIndexConfig,
    session: Any,
    records: list[dict[str, Any]],
    *,
    batch_size: int = 200,
    replace_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upload control records to OpenSearch using upsert semantics.

    Args:
        config: The AWSControlsIndexConfig object containing OpenSearch configuration.
        session: The boto3 session object for signing requests.
        records: A list of dictionaries representing control records to upload.
        batch_size: The number of records to process in each batch (default: 200).
        replace_existing: Whether to replace existing records with the same framework and version (default: False).
        dry_run: If True, perform a dry run without making any changes (default: False).

        Returns:
            A dictionary containing the results of the upload operation.
    """
    framework = str(records[0].get("framework", "")).strip()
    framework_version = str(records[0].get("framework_version", "")).strip()
    manifest_hash = _controls_manifest_hash(records)
    loaded_at = datetime.now(UTC).isoformat()

    if framework and framework_version:
        if any(
            str(r.get("framework", "")).strip() != framework
            or str(r.get("framework_version", "")).strip() != framework_version
            for r in records
        ):
            raise ValueError(
                "Records contain mixed framework/framework_version values; "
                "version-based duplicate detection requires a consistent batch."
            )

        existing_ids, existing_manifest_hashes = _search_existing_framework_version(
            config,
            session,
            framework,
            framework_version,
        )
        if existing_ids and manifest_hash in existing_manifest_hashes:
            return {
                "index_name": config.controls_index_name,
                "action": "skip_duplicate",
                "dry_run": dry_run,
                "records_total": len(records),
                "records_uploaded": 0,
                "records_failed": 0,
                "records_skipped": len(records),
                "skipped_reason": "framework_version_manifest_already_loaded",
                "framework": framework,
                "framework_version": framework_version,
                "manifest_hash": manifest_hash,
            }

        if existing_ids and not replace_existing:
            return {
                "index_name": config.controls_index_name,
                "action": "skip_conflict",
                "dry_run": dry_run,
                "records_total": len(records),
                "records_uploaded": 0,
                "records_failed": 0,
                "records_skipped": len(records),
                "skipped_reason": "framework_version_exists_with_different_manifest",
                "framework": framework,
                "framework_version": framework_version,
                "manifest_hash": manifest_hash,
                "existing_manifest_hashes": sorted(existing_manifest_hashes),
            }

        if existing_ids and replace_existing and dry_run:
            return {
                "index_name": config.controls_index_name,
                "action": "would_replace",
                "dry_run": True,
                "records_total": len(records),
                "records_uploaded": 0,
                "records_failed": 0,
                "records_skipped": 0,
                "records_would_upload": len(records),
                "records_would_delete": len(existing_ids),
                "framework": framework,
                "framework_version": framework_version,
                "manifest_hash": manifest_hash,
                "existing_manifest_hashes": sorted(existing_manifest_hashes),
            }

        if existing_ids and replace_existing:
            _bulk_delete_requirements(config, session, existing_ids)

    if dry_run:
        return {
            "index_name": config.controls_index_name,
            "action": "would_upload",
            "dry_run": True,
            "records_total": len(records),
            "records_uploaded": 0,
            "records_failed": 0,
            "records_skipped": 0,
            "records_would_upload": len(records),
            "framework": framework,
            "framework_version": framework_version,
            "manifest_hash": manifest_hash,
        }

    enriched_records: list[dict[str, Any]] = []
    for record in records:
        enriched = dict(record)
        if "control_applicability_scope" not in enriched:
            try:
                from ..assessment_orchestration import enrich_control_with_applicability

                enriched = enrich_control_with_applicability(enriched)
            except Exception:
                pass

        if _controls_embedding_enabled():
            embedding_text = _controls_embedding_text(enriched)
            if embedding_text:
                try:
                    vector = _embed_text_aws(embedding_text, session)
                except Exception:
                    vector = None
                if vector is not None:
                    enriched["embedding"] = vector

        enriched["ingestion_manifest_hash"] = manifest_hash
        enriched["ingestion_loaded_at"] = loaded_at
        enriched_records.append(enriched)

    uploaded = 0
    failed = 0
    bulk_url = f"{config.opensearch_endpoint.rstrip('/')}/_bulk?refresh=true"

    for i in range(0, len(enriched_records), batch_size):
        batch = enriched_records[i : i + batch_size]
        lines: list[str] = []
        for record in batch:
            req_id = str(record.get("requirement_id", "")).strip()
            if not req_id:
                failed += 1
                continue
            lines.append(
                json.dumps(
                    {"index": {"_index": config.controls_index_name, "_id": req_id}},
                    ensure_ascii=True,
                )
            )
            lines.append(json.dumps(record, ensure_ascii=True))

        if not lines:
            continue

        body = "\n".join(lines) + "\n"
        headers = _signed_headers(session, "POST", bulk_url, body)
        response = request_with_instrumentation(
            "POST",
            bulk_url,
            logger=logger,
            data=body,
            headers=headers,
            timeout=30,
            system="aws-opensearch",
            operation="bulk_upload_controls",
            request_callable=requests.post,
        )
        response.raise_for_status()
        payload = response.json()

        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            action = item.get("index", {})
            status = int(action.get("status", 500) or 500)
            if 200 <= status < 300:
                uploaded += 1
            else:
                failed += 1

    return {
        "index_name": config.controls_index_name,
        "action": "uploaded",
        "dry_run": False,
        "records_total": len(records),
        "records_uploaded": uploaded,
        "records_failed": failed,
        "records_skipped": 0,
        "framework": framework,
        "framework_version": framework_version,
        "manifest_hash": manifest_hash,
    }
