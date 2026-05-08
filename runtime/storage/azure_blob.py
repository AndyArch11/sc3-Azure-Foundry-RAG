"""Azure Blob Storage adapter implementing the StorageClient Protocol."""

from __future__ import annotations

import logging
from typing import Any

from azure.storage.blob import BlobServiceClient

from runtime.outbound_instrumentation import sdk_call_with_instrumentation

logger = logging.getLogger(__name__)


class AzureBlobStorageClient:
    """StorageClient backed by Azure Blob Storage."""

    def __init__(self, account_url: str, credential: Any) -> None:
        self._service_client = BlobServiceClient(
            account_url=account_url, credential=credential
        )

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        container = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_container_client",
            call=lambda: self._service_client.get_container_client(bucket_or_container),
        )
        sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="upload_blob",
            call=lambda: container.upload_blob(
                name=key,
                data=data,
                overwrite=True,
                metadata=metadata or {},
            ),
        )

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        container = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_container_client",
            call=lambda: self._service_client.get_container_client(bucket_or_container),
        )
        blobs = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="list_blobs",
            call=lambda: container.list_blobs(name_starts_with=prefix or None),
        )
        return [blob.name for blob in blobs if blob.name]

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, Any]:
        container = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_container_client",
            call=lambda: self._service_client.get_container_client(bucket_or_container),
        )
        blob_client = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_blob_client",
            call=lambda: container.get_blob_client(key),
        )
        props = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_blob_properties",
            call=blob_client.get_blob_properties,
        )
        raw: dict[str, Any] = dict(props.metadata or {})
        raw["content_length"] = props.size
        raw["content_type"] = props.content_settings.content_type
        raw["last_modified"] = (
            props.last_modified.isoformat() if props.last_modified else None
        )
        return raw

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        container = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_container_client",
            call=lambda: self._service_client.get_container_client(bucket_or_container),
        )
        stream = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="download_blob",
            call=lambda: container.download_blob(key),
        )
        return stream.readall()  # type: ignore[union-attr]

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        container = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_container_client",
            call=lambda: self._service_client.get_container_client(bucket_or_container),
        )
        sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="delete_blob",
            call=lambda: container.delete_blob(key, delete_snapshots="include"),
        )
