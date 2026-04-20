"""Local filesystem storage adapter for development and tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class LocalFileStorageClient:
    """StorageClient backed by a local filesystem directory tree."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir or os.getenv("LOCAL_STORAGE_BASE_DIR", "/tmp/local_storage"))

    def _blob_path(self, container: str, key: str) -> Path:
        return self._base / container / key

    def _meta_path(self, container: str, key: str) -> Path:
        return self._blob_path(container, key).with_suffix(".meta.json")

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        target = self._blob_path(bucket_or_container, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        meta: dict[str, Any] = dict(metadata or {})
        meta["content_length"] = len(data)
        self._meta_path(bucket_or_container, key).write_text(json.dumps(meta), encoding="utf-8")

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
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
        meta_path = self._meta_path(bucket_or_container, key)
        if not meta_path.exists():
            raise FileNotFoundError(f"No object '{key}' in '{bucket_or_container}'")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        target = self._blob_path(bucket_or_container, key)
        if target.exists():
            target.unlink()
        meta = self._meta_path(bucket_or_container, key)
        if meta.exists():
            meta.unlink()
