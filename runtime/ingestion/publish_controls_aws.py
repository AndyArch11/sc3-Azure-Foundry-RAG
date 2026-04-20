from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import requests

from .controls_index_aws import AWSControlsIndexConfig
from .publish_controls import _controls_manifest_hash


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


def _search_existing_framework_version(
    config: AWSControlsIndexConfig,
    session: Any,
    framework: str,
    framework_version: str,
) -> tuple[list[str], set[str]]:
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
    response = requests.post(search_url, data=body, headers=headers, timeout=30)
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
    response = requests.post(bulk_url, data=body, headers=headers, timeout=30)
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
    """Upload control records to OpenSearch using upsert semantics."""
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
        response = requests.post(bulk_url, data=body, headers=headers, timeout=30)
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
