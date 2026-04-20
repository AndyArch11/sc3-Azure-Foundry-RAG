"""Blob storage helpers for corpus management endpoints."""

from __future__ import annotations

import logging
from typing import Any

from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)


def _count_blob_prefix(prefix: str, *, svc: Any) -> dict[str, int]:
    """Count blobs under *prefix* without deleting them (dry-run support)."""
    if not svc._is_corpus_upload_enabled():
        return {"would_delete": 0}

    account_url = f"https://{svc.config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=svc.credential)
    container = client.get_container_client(svc.config.storage_container_name)
    count = 0
    try:
        blobs = container.list_blobs(name_starts_with=prefix)
        for blob in blobs:
            if blob.name:
                count += 1
    except Exception as exc:
        svc.logger.warning(f"Failed to count blobs with prefix {prefix}: {exc}")
    return {"would_delete": count}


def _delete_blob_prefix(prefix: str, *, svc: Any) -> dict[str, int]:
    """Delete all blobs under *prefix* and return a deletion count."""
    if not svc._is_corpus_upload_enabled():
        return {"deleted": 0}

    account_url = f"https://{svc.config.storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=svc.credential)
    container = client.get_container_client(svc.config.storage_container_name)
    deleted = 0
    try:
        blobs = container.list_blobs(name_starts_with=prefix)
        for blob in blobs:
            if blob.name:
                container.delete_blob(blob.name)
                deleted += 1
    except Exception as exc:
        svc.logger.warning(f"Failed to delete blobs with prefix {prefix}: {exc}")
    return {"deleted": deleted}
