"""AWS S3 storage adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class AWSS3StorageClient:
    """StorageClient backed by Amazon S3."""

    def __init__(self, region_name: str | None = None, session: Any = None) -> None:
        """Initialise the S3 client with optional region and session.
        Args:
            region_name: Optional AWS region name for S3 storage.
            session: Optional AWS session for S3 storage.
        """
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
        """Upload object data to S3.
        Args:
            bucket_or_container: The name of the S3 bucket.
            key: The key of the object.
            data: The object data as bytes.
            metadata: Optional metadata for the object.
        """
        kwargs: dict[str, Any] = {"Bucket": bucket_or_container, "Key": key, "Body": data}
        if metadata:
            kwargs["Metadata"] = metadata
        self._s3.put_object(**kwargs)

    def list_objects(self, bucket_or_container: str, prefix: str = "") -> list[str]:
        """List object keys in an S3 bucket with an optional prefix.
        Args:
            bucket_or_container: The name of the S3 bucket.
            prefix: Optional prefix to filter object keys.
        Returns:
            A list of object keys that match the specified prefix.
        """
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
        """Return metadata for a stored object in S3.
        Args:
            bucket_or_container: The name of the S3 bucket.
            key: The key of the object.
        Returns:
            A dictionary containing the metadata of the object.
        """
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
        """Download object content from S3.
        Args:
            bucket_or_container: The name of the S3 bucket.
            key: The key of the object.
        Returns:
            The object data as bytes.
        """
        response = self._s3.get_object(Bucket=bucket_or_container, Key=key)
        return response["Body"].read()

    def delete_object(self, bucket_or_container: str, key: str) -> None:
        """Delete an object from S3.
        Args:
            bucket_or_container: The name of the S3 bucket.
            key: The key of the object to delete.
        """
        self._s3.delete_object(Bucket=bucket_or_container, Key=key)
