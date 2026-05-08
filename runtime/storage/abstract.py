"""Cloud-agnostic storage client contract."""

from __future__ import annotations

from typing import Any, Protocol


class StorageClient(Protocol):
    """Provider-neutral object storage operations."""

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Upload object data to storage."""

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        """List object keys for a bucket/container prefix."""

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, Any]:
        """Return metadata for a stored object."""

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        """Download and return object data from storage."""

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        """Delete a stored object."""
