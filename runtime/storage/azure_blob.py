"""Azure Blob Storage adapter implementing the StorageClient Protocol."""

from __future__ import annotations

from typing import Any

from azure.storage.blob import BlobServiceClient


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
        container = self._service_client.get_container_client(bucket_or_container)
        container.upload_blob(
            name=key,
            data=data,
            overwrite=True,
            metadata=metadata or {},
        )

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        container = self._service_client.get_container_client(bucket_or_container)
        blobs = container.list_blobs(name_starts_with=prefix or None)
        return [blob.name for blob in blobs if blob.name]

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, Any]:
        container = self._service_client.get_container_client(bucket_or_container)
        blob_client = container.get_blob_client(key)
        props = blob_client.get_blob_properties()
        raw: dict[str, Any] = dict(props.metadata or {})
        raw["content_length"] = props.size
        raw["content_type"] = props.content_settings.content_type
        raw["last_modified"] = (
            props.last_modified.isoformat() if props.last_modified else None
        )
        return raw

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        container = self._service_client.get_container_client(bucket_or_container)
        container.delete_blob(key, delete_snapshots="include")
