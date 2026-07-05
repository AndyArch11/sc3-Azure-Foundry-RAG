"""
Blob uploader for ingestion runner.

This module provides functionality to upload source files to Azure Blob Storage for ingestion purposes.
It supports various file types and allows for metadata to be associated with each uploaded blob.
The uploader can handle overwriting existing blobs and provides a summary of the upload process, including successfully uploaded files,
skipped files (due to unsupported extensions), and any failures encountered during the upload.
The uploader is designed to be used in the context of an ingestion runner, where files are processed and uploaded to a specified Azure Blob Storage container.
The uploader also generates a unique upload batch identifier for each run, which can be used to track and manage uploaded files.
The uploader is intended to be used in conjunction with other components of the ingestion pipeline, such as parsers and processors, to facilitate the ingestion of source files into a structured format for further analysis and processing.
The uploader is designed to be robust and handle various edge cases, such as missing files, unsupported file types, and errors during the upload process.
It provides clear feedback on the status of each file processed, allowing for easy identification of any issues that may arise during the upload process.
The uploader is implemented using the Azure SDK for Python, specifically the azure-storage-blob package, which provides a convenient interface for interacting with Azure Blob Storage.
It leverages the BlobServiceClient class to create a client for the specified storage account and container, and uses the upload_blob method to upload files to the container.
The uploader also supports the use of a TokenCredential for authentication, allowing for secure access to the storage account.

"""

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
    """UploadSummary.

    Attributes:
        uploaded: List of successfully uploaded blob names.
        skipped: List of file paths that were skipped due to unsupported extensions.
        failed: List of blob names that failed to upload, along with the associated error message.
    """

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
    """Run upload source files.

    Args:
        storage_account_name: The name of the Azure storage account.
        container_name: The name of the Azure Blob Storage container.
        input_dir: The directory containing the source files to upload.
        credential: The TokenCredential for authentication with Azure Blob Storage.
        overwrite: Whether to overwrite existing blobs with the same name. Default is True.
        corpus: The corpus identifier for the uploaded files. Default is "b".
        corpus_role: The role of the corpus for the uploaded files. Default is "narrative_guidance".
        upload_source: The source of the upload. Default is "ingestion_runner".
        uploaded_by: The identifier of the uploader. Default is "ingestion_job".
        upload_batch: An optional batch identifier for the upload. If not provided, a new UUID will be generated.

    Returns:
        An UploadSummary object containing the results of the upload process, including lists of successfully uploaded files, skipped files, and failed uploads.
    """
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
