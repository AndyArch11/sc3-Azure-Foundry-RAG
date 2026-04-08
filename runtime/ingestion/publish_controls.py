from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azure.core.credentials import TokenCredential
from azure.search.documents import SearchClient

from .controls_index import ControlsIndexConfig

REQUIRED_FIELDS = {
    "requirement_id",
    "framework",
    "framework_version",
    "control_family",
    "maturity_level",
    "requirement_text",
    "guidance_text",
    "keywords",
    "source_uri",
    "source_section",
    "effective_date",
    "jurisdiction_or_scope",
}

OPTIONAL_APPLICABILITY_FIELDS = {
    "control_applicability_scope",
    "applicability_confidence",
    "applicability_uncertain",
}


def load_controls_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc

            if not isinstance(record, dict):
                raise ValueError(f"Invalid record at line {line_no}: expected JSON object")

            missing = REQUIRED_FIELDS - set(record.keys())
            if missing:
                missing_str = ", ".join(sorted(missing))
                raise ValueError(f"Record at line {line_no} missing fields: {missing_str}")

            records.append(record)

    if not records:
        raise ValueError(f"No records found in JSONL file: {path}")

    return records


def _batched(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _controls_manifest_hash(records: list[dict[str, Any]]) -> str:
    """Compute a stable manifest hash for a framework/version payload."""
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


def _framework_version_state(
    client: SearchClient,
    framework: str,
    framework_version: str,
) -> tuple[list[str], set[str]]:
    escaped_framework = framework.replace("'", "''")
    escaped_version = framework_version.replace("'", "''")
    filter_expr = (
        f"framework eq '{escaped_framework}' "
        f"and framework_version eq '{escaped_version}'"
    )

    requirement_ids: list[str] = []
    manifest_hashes: set[str] = set()
    pager = client.search(
        search_text="*",
        filter=filter_expr,
        top=1000,
        select=["requirement_id", "ingestion_manifest_hash"],
    )
    for item in pager:
        req_id = str(item.get("requirement_id", "")).strip()
        if req_id:
            requirement_ids.append(req_id)
        manifest = str(item.get("ingestion_manifest_hash", "")).strip()
        if manifest:
            manifest_hashes.add(manifest)

    return requirement_ids, manifest_hashes


def _delete_requirements(client: SearchClient, requirement_ids: list[str], batch_size: int = 500) -> None:
    if not requirement_ids:
        return
    for batch in [requirement_ids[i : i + batch_size] for i in range(0, len(requirement_ids), batch_size)]:
        docs = [{"requirement_id": req_id} for req_id in batch]
        client.delete_documents(docs)


def upload_controls_records(
    config: ControlsIndexConfig,
    credential: TokenCredential,
    records: list[dict[str, Any]],
    *,
    batch_size: int = 200,
    replace_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upload control records to Azure AI Search using upsert semantics."""
    client = SearchClient(
        endpoint=config.search_endpoint,
        index_name=config.controls_index_name,
        credential=credential,
    )

    # Corpus A duplicate gate: use (framework, framework_version, manifest_hash)
    # for deterministic duplicate detection, with optional replacement flow.
    framework = str(records[0].get("framework", "")).strip()
    framework_version = str(records[0].get("framework_version", "")).strip()
    manifest_hash = _controls_manifest_hash(records)
    loaded_at = datetime.now(UTC).isoformat()

    enriched_records = []
    for record in records:
        enriched = dict(record)
        
        # Apply control applicability enrichment if not already present
        if "control_applicability_scope" not in enriched:
            try:
                from ..assessment_orchestration import enrich_control_with_applicability
                enriched = enrich_control_with_applicability(enriched)
            except Exception:
                # Fallback: skip enrichment if unavailable (backward compatibility)
                pass
        
        enriched["ingestion_manifest_hash"] = manifest_hash
        enriched["ingestion_loaded_at"] = loaded_at
        enriched_records.append(enriched)

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

        existing_ids, existing_manifest_hashes = _framework_version_state(
            client,
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
            _delete_requirements(client, existing_ids)

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

    uploaded = 0
    failed = 0

    for batch in _batched(enriched_records, batch_size):
        result = client.merge_or_upload_documents(batch)
        uploaded += sum(1 for item in result if item.succeeded)
        failed += sum(1 for item in result if not item.succeeded)

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
