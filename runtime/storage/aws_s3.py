"""AWS S3 storage adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class AWSS3StorageClient:
    """StorageClient backed by Amazon S3."""

    def __init__(self, region_name: str | None = None, session: Any = None) -> None:
        if session is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for AWS storage provider but is not installed"
                ) from exc
            _session = boto3.Session(region_name=region_name)
        else:
            _session = session
        self._s3 = _session.client("s3")

    def put_object(
        self,
        bucket_or_container: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"Bucket": bucket_or_container, "Key": key, "Body": data}
        if metadata:
            kwargs["Metadata"] = metadata
        self._s3.put_object(**kwargs)

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        kwargs: dict[str, Any] = {"Bucket": bucket_or_container}
        if prefix:
            kwargs["Prefix"] = prefix

        paginator = self._s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []):
                key = obj.get("Key")
                if isinstance(key, str):
                    keys.append(key)
        return keys

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, Any]:
        response = self._s3.head_object(Bucket=bucket_or_container, Key=key)
        raw: dict[str, Any] = dict(response.get("Metadata") or {})
        raw["content_length"] = response.get("ContentLength")
        raw["content_type"] = response.get("ContentType")
        last_modified = response.get("LastModified")
        raw["last_modified"] = (
            last_modified.isoformat() if isinstance(last_modified, datetime) else None
        )
        if "ETag" in response:
            raw["etag"] = response.get("ETag")
        if "VersionId" in response:
            raw["version_id"] = response.get("VersionId")
        return raw

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        """Download object content from S3."""
        response = self._s3.get_object(Bucket=bucket_or_container, Key=key)
        return response["Body"].read()

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        self._s3.delete_object(Bucket=bucket_or_container, Key=key)
