"""
ingestion publish controls
- publish controls to Azure AI Search or AWS OpenSearch
- supports optional embedding of control text for semantic search
- supports optional enrichment of control applicability via Foundry assessment orchestration
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
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


def _controls_embedding_enabled() -> bool:
    """Check if control embedding is enabled based on environment variable.
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
        A string containing the text to be used for embedding.
    """
    requirement_text = str(record.get("requirement_text") or "").strip()
    guidance_text = str(record.get("guidance_text") or "").strip()
    keywords = record.get("keywords") or []
    keyword_text = " ".join(str(k).strip() for k in keywords if str(k).strip())

    combined = "\n".join(
        part for part in [requirement_text, guidance_text, keyword_text] if part
    ).strip()
    return combined[:6000]


def _embed_text_azure(text: str, credential: TokenCredential) -> list[float] | None:
    """Generate an embedding vector for the given text using Azure OpenAI.
    Args:
        text: The input text to embed.
        credential: An Azure TokenCredential for authentication.
    Returns:
        A list of floats representing the embedding vector, or None if embedding is not available.
    """
    endpoint = (
        os.getenv("AZURE_OPENAI_ENDPOINT", "").strip() or os.getenv("OPENAI_ENDPOINT", "").strip()
    ).rstrip("/")
    deployment = os.getenv("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-ada-002").strip()
    if not endpoint or not deployment:
        return None

    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    url = f"{endpoint}/openai/deployments/{deployment}" f"/embeddings?api-version=2023-05-15"
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"input": text},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    vector = payload.get("data", [{}])[0].get("embedding")
    if not isinstance(vector, list) or not vector:
        raise RuntimeError("Azure embedding response did not include a vector")
    return [float(v) for v in vector]


def load_controls_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load control records from a JSONL file.

    Args:
        path: The path to the JSONL file.

    Returns:
        A list of dictionaries representing control records.
    """
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
    """Run batched.

    Args:
        items: A list of items to batch.
        batch_size: The maximum size of each batch.

    Returns:
        A list of batches, where each batch is a list of items.
    """
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _controls_manifest_hash(records: list[dict[str, Any]]) -> str:
    """Compute a stable manifest hash for a framework/version payload.

    Args:
        records: A list of control records.

    Returns:
        A string representing the stable manifest hash.
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


def _framework_version_state(
    client: SearchClient,
    framework: str,
    framework_version: str,
) -> tuple[list[str], set[str]]:
    """Run framework version state.

    Args:
        client: The SearchClient object for interacting with Azure AI Search.
        framework: The framework name.
        framework_version: The framework version.

    Returns:
        A tuple containing a list of requirement IDs and a set of manifest hashes.
    """
    escaped_framework = framework.replace("'", "''")
    escaped_version = framework_version.replace("'", "''")
    filter_expr = (
        f"framework eq '{escaped_framework}' " f"and framework_version eq '{escaped_version}'"
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


def _delete_requirements(
    client: SearchClient, requirement_ids: list[str], batch_size: int = 500
) -> None:
    """Run delete requirements.

    Args:
        client: The SearchClient object for interacting with Azure AI Search.
        requirement_ids: A list of requirement IDs to delete.
        batch_size: The maximum number of requirements to delete in a single batch.
    """
    if not requirement_ids:
        return
    for batch in [
        requirement_ids[i : i + batch_size] for i in range(0, len(requirement_ids), batch_size)
    ]:
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
    """Upload control records to Azure AI Search using upsert semantics.

    Args:
        config: The ControlsIndexConfig object containing index configuration.
        credential: The TokenCredential object for authentication.
        records: A list of control records to upload.
        batch_size: The maximum number of records to upload in a single batch.
        replace_existing: Whether to replace existing records with the same framework/version.
        dry_run: If True, do not actually upload records, just simulate the operation.

    Returns:
        A dictionary containing the upload results.
    """
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

    enriched_records = []
    for record in records:
        enriched = dict(record)

        # Apply control applicability enrichment if not already present.
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
                    vector = _embed_text_azure(embedding_text, credential)
                except Exception:
                    vector = None
                if vector is not None:
                    enriched["content_vector"] = vector

        enriched["ingestion_manifest_hash"] = manifest_hash
        enriched["ingestion_loaded_at"] = loaded_at
        enriched_records.append(enriched)

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
