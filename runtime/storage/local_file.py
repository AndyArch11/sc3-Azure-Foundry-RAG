"""Local filesystem storage adapter for development and tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class LocalFileStorageClient:
    """StorageClient backed by a local filesystem directory tree."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """Initialise the local file storage client.

        Args:
            base_dir: Optional base directory for local file storage. If not provided, the function will attempt to read from the "LOCAL_STORAGE_BASE_DIR" environment variable. If neither is provided, defaults to "/tmp/local_storage".
        """
        env_base_dir = os.getenv("LOCAL_STORAGE_BASE_DIR")
        resolved_base_dir: str | Path = base_dir or env_base_dir or "/tmp/local_storage"
        self._base = Path(resolved_base_dir)

    def _blob_path(self, container: str, key: str) -> Path:
        """Return the full path to the blob file for a given container and key.

        Args:
            container: The name of the container (directory).
            key: The key of the object (file path relative to the container).
        Returns:
            The full Path object representing the blob file location.
        """
        return self._base / container / key

    def _meta_path(self, container: str, key: str) -> Path:
        """Return the full path to the metadata file for a given container and key.

        Args:
            container: The name of the container (directory).
            key: The key of the object (file path relative to the container).
        Returns:
            The full Path object representing the metadata file location.
        """
        return self._blob_path(container, key).with_suffix(".meta.json")

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Upload object data to local filesystem storage.
        
        Args:
            bucket_or_container: The name of the container (directory).
            key: The key of the object (file path relative to the container).
            data: The object data as bytes.
            metadata: Optional metadata for the object.
    """
        target = self._blob_path(bucket_or_container, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        meta: dict[str, Any] = dict(metadata or {})
        meta["content_length"] = len(data)
        self._meta_path(bucket_or_container, key).write_text(json.dumps(meta), encoding="utf-8")

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        """List object keys in a local filesystem container with an optional prefix.

        Args:
            bucket_or_container: The name of the container (directory).
            prefix: Optional prefix to filter object keys.
        Returns:
            A list of object keys that match the specified prefix.
        """
        container_dir = self._base / bucket_or_container
        if not container_dir.exists():
            return []
        results: list[str] = []
        for path in container_dir.rglob("*"):
            if path.is_file() and not path.name.endswith(".meta.json"):
                rel = str(path.relative_to(container_dir))
                if not prefix or rel.startswith(prefix):
                    results.append(rel)
        return sorted(results)

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, Any]:
        """Return metadata for a stored object in local filesystem storage.

        Args:
            bucket_or_container: The name of the container (directory).
            key: The key of the object (file path relative to the container).
        Returns:
            A dictionary containing the metadata of the object.
        """
        meta_path = self._meta_path(bucket_or_container, key)
        if not meta_path.exists():
            raise FileNotFoundError(f"No object '{key}' in '{bucket_or_container}'")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        """Return the data of a stored object in local filesystem storage.

        Args:
            bucket_or_container: The name of the container (directory).
            key: The key of the object (file path relative to the container).
        Returns:
            The object data as bytes.
        """
        target = self._blob_path(bucket_or_container, key)
        if not target.exists():
            raise FileNotFoundError(f"No object '{key}' in '{bucket_or_container}'")
        return target.read_bytes()

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        """Delete a stored object from local filesystem storage.

        Args:
            bucket_or_container: The name of the container (directory).
            key: The key of the object (file path relative to the container).
        """
        target = self._blob_path(bucket_or_container, key)
        if target.exists():
            target.unlink()
        meta = self._meta_path(bucket_or_container, key)
        if meta.exists():
            meta.unlink()
