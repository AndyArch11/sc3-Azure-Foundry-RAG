"""Blob storage helpers for corpus management endpoints."""

from __future__ import annotations

import logging
from typing import Any

from runtime.provider_core import normalise_cloud_provider

_BlobServiceClientImpl: Any

try:
    from azure.storage.blob import BlobServiceClient as _ImportedBlobServiceClient

    _BlobServiceClientImpl = _ImportedBlobServiceClient
except Exception:

    class _MissingAzureSdkClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(
                "Azure SDK packages are not installed in this runtime. "
                "Azure blob operations are unavailable."
            )

    _BlobServiceClientImpl = _MissingAzureSdkClient

BlobServiceClient: Any = _BlobServiceClientImpl


logger = logging.getLogger(__name__)


def _create_blob_service_client(*, account_url: str, credential: Any) -> Any:
    """Create a BlobServiceClient using the imported SDK or the fallback client.

    The imported alias is kept as ``Any`` so mypy can accept either the real
    Azure SDK class or the local placeholder used when the SDK is unavailable.
    """
    return BlobServiceClient(account_url=account_url, credential=credential)


def _resolve_blob_provider(svc: Any) -> str:
    """Resolve the cloud provider used to decide which blob backend to hit.

    Falls back to 'azure' when the provider cannot be determined, matching
    this module's historical default behaviour.
    """
    try:
        return normalise_cloud_provider(getattr(getattr(svc, "config", None), "cloud_provider", ""))
    except Exception:
        return "azure"


def _count_blob_prefix(prefix: str, *, svc: Any) -> dict[str, int]:
    """Count blobs/objects under *prefix* without deleting them (dry-run support).

    Args:
        prefix: The prefix of the blobs/objects to count.
        svc: The service object providing access to configuration and logging.

    Returns:
        A dictionary with a single key "would_delete" indicating the count of
        blobs/objects under the specified prefix. Always zero for providers
        (e.g. local) that do not back corpus uploads with blob/object storage.
    """
    provider = _resolve_blob_provider(svc)

    if provider == "aws":
        if not svc._is_corpus_upload_enabled():
            return {"would_delete": 0}
        from runtime.storage.aws_s3 import AWSS3StorageClient

        try:
            client = AWSS3StorageClient(session=svc.credential)
            keys = client.list_objects(svc.config.s3_bucket_name, prefix=prefix)
        except Exception as exc:
            svc.logger.warning(f"Failed to count S3 objects with prefix {prefix}: {exc}")
            return {"would_delete": 0}
        return {"would_delete": len(keys)}

    if provider != "azure":
        return {"would_delete": 0}

    if not svc._is_corpus_upload_enabled():
        return {"would_delete": 0}

    account_url = f"https://{svc.config.storage_account_name}.blob.core.windows.net"
    client = _create_blob_service_client(account_url=account_url, credential=svc.credential)
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
    """Delete all blobs/objects under *prefix* and return a deletion count.

    Args:
        prefix: The prefix of the blobs/objects to delete.
        svc: The service object providing access to configuration and logging.

    Returns:
        A dictionary containing the number of blobs/objects deleted. Always
        zero for providers (e.g. local) that do not back corpus uploads with
        blob/object storage.
    """
    provider = _resolve_blob_provider(svc)

    if provider == "aws":
        if not svc._is_corpus_upload_enabled():
            return {"deleted": 0}
        from runtime.storage.aws_s3 import AWSS3StorageClient

        deleted = 0
        try:
            client = AWSS3StorageClient(session=svc.credential)
            keys = client.list_objects(svc.config.s3_bucket_name, prefix=prefix)
            for key in keys:
                client.delete_object(svc.config.s3_bucket_name, key)
                deleted += 1
        except Exception as exc:
            svc.logger.warning(f"Failed to delete S3 objects with prefix {prefix}: {exc}")
        return {"deleted": deleted}

    if provider != "azure":
        return {"deleted": 0}

    if not svc._is_corpus_upload_enabled():
        return {"deleted": 0}

    account_url = f"https://{svc.config.storage_account_name}.blob.core.windows.net"
    client = _create_blob_service_client(account_url=account_url, credential=svc.credential)
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
