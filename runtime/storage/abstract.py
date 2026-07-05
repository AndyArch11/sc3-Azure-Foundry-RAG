"""Cloud-agnostic storage client contract."""

from __future__ import annotations

from typing import Any, Protocol


class StorageClient(Protocol):
    """Provider-neutral object storage operations.
    
    Attributes:
        put_object: Method to upload object data to storage.
        list_objects: Method to list object keys for a bucket/container prefix.
        get_object_metadata: Method to return metadata for a stored object.
        get_object: Method to download and return object data from storage.
        delete_object: Method to delete a stored object.
    """

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Upload object data to storage.
        Args:
            bucket_or_container: The name of the bucket or container.
            key: The key of the object.
            data: The object data as bytes.
            metadata: Optional metadata for the object.
        """
        ...

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        """List object keys for a bucket/container prefix.
        Args:
            bucket_or_container: The name of the bucket or container.
            prefix: Optional prefix to filter object keys.
        Returns:
            A list of object keys that match the specified prefix.
        """
        ...

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, Any]:
        """Return metadata for a stored object.
        Args:
            bucket_or_container: The name of the bucket or container.
            key: The key of the object.
        Returns:
            A dictionary containing the metadata of the object.
        """
        ...

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        """Download and return object data from storage.
        Args:
            bucket_or_container: The name of the bucket or container.
            key: The key of the object.
        Returns:
            The object data as bytes.
        """
        ...

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        """Delete a stored object.
        Args:
            bucket_or_container: The name of the bucket or container.
            key: The key of the object to delete.
        """
        ...
