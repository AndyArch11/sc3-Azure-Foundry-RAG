from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from azure.core.credentials import TokenCredential
from azure.storage.blob import BlobServiceClient

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".xlsx",
    ".xlsm",
    ".xltx",
    ".xltm",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".html",
}


@dataclass
class UploadSummary:
    """UploadSummary."""

    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def upload_source_files(
    storage_account_name: str,
    container_name: str,
    input_dir: Path,
    credential: TokenCredential,
    overwrite: bool = True,
    *,
    corpus: str = "b",
    corpus_role: str = "narrative_guidance",
    upload_source: str = "ingestion_runner",
    uploaded_by: str = "ingestion_job",
    upload_batch: str | None = None,
) -> UploadSummary:
    """Run upload source files."""
    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container_client = client.get_container_client(container_name)
    uploaded_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    effective_upload_batch = str(upload_batch or "").strip() or str(uuid4())

    summary = UploadSummary()
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            summary.skipped.append(str(path))
            continue
        blob_name = path.relative_to(input_dir).as_posix()
        try:
            content = path.read_bytes()
            content_sha256 = hashlib.sha256(content).hexdigest()
            metadata = {
                "corpus": corpus,
                "corpus_role": corpus_role,
                "upload_source": upload_source,
                "uploaded_by": uploaded_by,
                "upload_batch": effective_upload_batch,
                "uploaded_at": uploaded_at,
                "original_filename": path.name,
                # For this loader path, binary hash is the canonical dedupe key.
                "content_sha256": content_sha256,
                "normalised_text_sha256": "",
                "dedupe_hash": content_sha256,
                "dedupe_method": "content_sha256",
            }
            container_client.upload_blob(
                blob_name,
                content,
                overwrite=overwrite,
                metadata=metadata,
            )
            summary.uploaded.append(blob_name)
        except Exception as exc:
            summary.failed.append(f"{blob_name}: {exc}")

    return summary
