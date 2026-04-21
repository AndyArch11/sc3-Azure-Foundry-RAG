"""Unit tests for storage abstraction: factory dispatch and local adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.storage import get_storage_client
from runtime.storage.local_file import LocalFileStorageClient

# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


class TestStorageFactory:
    def test_local_provider(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        client = get_storage_client(base_dir=str(tmp_path))
        assert isinstance(client, LocalFileStorageClient)

    def test_dev_alias(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "dev")
        client = get_storage_client(base_dir=str(tmp_path))
        assert isinstance(client, LocalFileStorageClient)

    def test_argument_overrides_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        client = get_storage_client(cloud_provider="local", base_dir=str(tmp_path))
        assert isinstance(client, LocalFileStorageClient)

    def test_azure_requires_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
        with pytest.raises(ValueError, match="account_url"):
            get_storage_client(cloud_provider="azure")

    def test_azure_factory_returns_azure_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from runtime.storage.azure_blob import AzureBlobStorageClient

        mock_bsc = MagicMock()
        with patch("runtime.storage.azure_blob.BlobServiceClient", return_value=mock_bsc):
            client = get_storage_client(
                cloud_provider="azure",
                credential=MagicMock(),
                account_url="https://account.blob.core.windows.net",
            )
        assert isinstance(client, AzureBlobStorageClient)

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="Unsupported cloud provider"):
            get_storage_client(cloud_provider="gcp")

    def test_aws_factory_returns_s3_client(self) -> None:
        from runtime.storage.aws_s3 import AWSS3StorageClient

        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.Session.return_value = mock_session

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            client = get_storage_client(cloud_provider="aws")
        assert isinstance(client, AWSS3StorageClient)


# ---------------------------------------------------------------------------
# LocalFileStorageClient – round-trip contract
# ---------------------------------------------------------------------------


class TestLocalFileStorageClient:
    def test_put_and_list(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        client.put_object("my-bucket", "docs/file.txt", b"hello world")
        keys = client.list_objects("my-bucket")
        assert "docs/file.txt" in keys

    def test_put_with_prefix_filter(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        client.put_object("bucket", "a/one.txt", b"a")
        client.put_object("bucket", "b/two.txt", b"b")
        keys_a = client.list_objects("bucket", prefix="a/")
        assert "a/one.txt" in keys_a
        assert "b/two.txt" not in keys_a

    def test_get_metadata(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        client.put_object("bucket", "file.bin", b"\x00\x01\x02", metadata={"source": "test"})
        meta = client.get_object_metadata("bucket", "file.bin")
        assert meta["source"] == "test"
        assert meta["content_length"] == 3

    def test_delete_removes_object(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        client.put_object("bucket", "gone.txt", b"data")
        assert "gone.txt" in client.list_objects("bucket")
        client.delete_object("bucket", "gone.txt")
        assert "gone.txt" not in client.list_objects("bucket")

    def test_delete_nonexistent_is_no_op(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        # Should not raise
        client.delete_object("bucket", "does_not_exist.txt")

    def test_list_empty_container(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        assert client.list_objects("empty-bucket") == []

    def test_metadata_not_found_raises(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError):
            client.get_object_metadata("bucket", "missing.txt")

    def test_overwrite_replaces_content(self, tmp_path: Path) -> None:
        client = LocalFileStorageClient(base_dir=str(tmp_path))
        client.put_object("bucket", "file.txt", b"first")
        client.put_object("bucket", "file.txt", b"second")
        meta = client.get_object_metadata("bucket", "file.txt")
        assert meta["content_length"] == 6


# ---------------------------------------------------------------------------
# AzureBlobStorageClient – mocked interactions
# ---------------------------------------------------------------------------


class TestAzureBlobStorageClient:
    def _make_client(self) -> Any:
        from runtime.storage.azure_blob import AzureBlobStorageClient

        mock_bsc = MagicMock()
        with patch("runtime.storage.azure_blob.BlobServiceClient", return_value=mock_bsc):
            client = AzureBlobStorageClient(
                account_url="https://account.blob.core.windows.net",
                credential=MagicMock(),
            )
        return client

    def test_put_object_calls_upload_blob(self) -> None:
        client = self._make_client()
        mock_container = MagicMock()
        client._service_client.get_container_client.return_value = mock_container
        client.put_object("cont", "key.txt", b"data", metadata={"tag": "v1"})
        mock_container.upload_blob.assert_called_once_with(
            name="key.txt", data=b"data", overwrite=True, metadata={"tag": "v1"}
        )

    def test_list_objects_returns_names(self) -> None:
        client = self._make_client()
        mock_container = MagicMock()
        blob_a = MagicMock()
        blob_a.name = "a.txt"
        blob_b = MagicMock()
        blob_b.name = "b.txt"
        mock_container.list_blobs.return_value = [blob_a, blob_b]
        client._service_client.get_container_client.return_value = mock_container
        result = client.list_objects("cont")
        assert result == ["a.txt", "b.txt"]

    def test_delete_object_calls_delete_blob(self) -> None:
        client = self._make_client()
        mock_container = MagicMock()
        client._service_client.get_container_client.return_value = mock_container
        client.delete_object("cont", "key.txt")
        mock_container.delete_blob.assert_called_once_with("key.txt", delete_snapshots="include")


# ---------------------------------------------------------------------------
# AWSS3StorageClient – mocked interactions
# ---------------------------------------------------------------------------


class TestAWSS3StorageClient:
    def _make_client(self) -> Any:
        from runtime.storage.aws_s3 import AWSS3StorageClient

        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        return AWSS3StorageClient(session=mock_session)

    def test_list_objects_uses_paginator(self) -> None:
        client = self._make_client()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "a.txt"}]},
            {"Contents": [{"Key": "b.txt"}]},
        ]
        client._s3.get_paginator.return_value = paginator

        keys = client.list_objects("bucket", prefix="docs/")

        client._s3.get_paginator.assert_called_once_with("list_objects_v2")
        paginator.paginate.assert_called_once_with(Bucket="bucket", Prefix="docs/")
        assert keys == ["a.txt", "b.txt"]

    def test_get_object_metadata_normalizes_fields(self) -> None:
        from datetime import UTC, datetime

        client = self._make_client()
        client._s3.head_object.return_value = {
            "Metadata": {"source": "unit-test"},
            "ContentLength": 42,
            "ContentType": "text/plain",
            "LastModified": datetime(2026, 4, 20, 10, 11, 12, tzinfo=UTC),
            "ETag": '"abc"',
            "VersionId": "v1",
        }

        meta = client.get_object_metadata("bucket", "key.txt")

        assert meta["source"] == "unit-test"
        assert meta["content_length"] == 42
        assert meta["content_type"] == "text/plain"
        assert meta["last_modified"] == "2026-04-20T10:11:12+00:00"
        assert meta["etag"] == '"abc"'
        assert meta["version_id"] == "v1"
