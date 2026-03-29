from __future__ import annotations

import json
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


def upload_controls_records(
    config: ControlsIndexConfig,
    credential: TokenCredential,
    records: list[dict[str, Any]],
    *,
    batch_size: int = 200,
) -> dict[str, Any]:
    """Upload control records to Azure AI Search using upsert semantics."""
    client = SearchClient(
        endpoint=config.search_endpoint,
        index_name=config.controls_index_name,
        credential=credential,
    )

    uploaded = 0
    failed = 0

    for batch in _batched(records, batch_size):
        result = client.merge_or_upload_documents(batch)
        uploaded += sum(1 for item in result if item.succeeded)
        failed += sum(1 for item in result if not item.succeeded)

    return {
        "index_name": config.controls_index_name,
        "records_total": len(records),
        "records_uploaded": uploaded,
        "records_failed": failed,
    }
