from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from azure.core.credentials import TokenCredential
from azure.storage.blob import BlobServiceClient

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xlsm", ".xltx", ".xltm", ".docx", ".doc", ".pptx", ".ppt", ".html"}


@dataclass
class UploadSummary:
    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def upload_source_files(
    storage_account_name: str,
    container_name: str,
    input_dir: Path,
    credential: TokenCredential,
    overwrite: bool = True,
) -> UploadSummary:
    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container_client = client.get_container_client(container_name)

    summary = UploadSummary()
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            summary.skipped.append(str(path))
            continue
        blob_name = path.relative_to(input_dir).as_posix()
        try:
            with path.open("rb") as f:
                container_client.upload_blob(blob_name, f, overwrite=overwrite)
            summary.uploaded.append(blob_name)
        except Exception as exc:
            summary.failed.append(f"{blob_name}: {exc}")

    return summary
