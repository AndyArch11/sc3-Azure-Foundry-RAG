"""Unit tests for query_web/pipeline/storage.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from query_web.pipeline import storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_svc(*, upload_enabled: bool = True, blobs: list[str] | None = None):
    """Build a minimal svc stub matching what storage.py expects."""
    svc = MagicMock()
    svc._is_corpus_upload_enabled.return_value = upload_enabled
    svc.config.storage_account_name = "myaccount"
    svc.config.storage_container_name = "grounding-data"
    svc.config.cloud_provider = "azure"
    svc.credential = MagicMock()
    svc.logger = MagicMock()
    return svc


def _make_aws_svc(*, upload_enabled: bool = True):
    """Build a minimal svc stub for the AWS (S3) blob backend."""
    svc = MagicMock()
    svc._is_corpus_upload_enabled.return_value = upload_enabled
    svc.config.cloud_provider = "aws"
    svc.config.s3_bucket_name = "my-bucket"
    svc.credential = MagicMock()
    svc.logger = MagicMock()
    return svc


def _make_blob(name: str):
    b = MagicMock()
    b.name = name
    return b


# ---------------------------------------------------------------------------
# _count_blob_prefix
# ---------------------------------------------------------------------------


def test_count_blob_prefix_returns_zero_when_upload_disabled():
    svc = _make_svc(upload_enabled=False)
    result = storage._count_blob_prefix("prefix/", svc=svc)
    assert result == {"would_delete": 0}


def test_count_blob_prefix_counts_matching_blobs():
    svc = _make_svc()
    blobs = [_make_blob("prefix/a.txt"), _make_blob("prefix/b.txt"), _make_blob("prefix/c.txt")]

    mock_container = MagicMock()
    mock_container.list_blobs.return_value = blobs
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch("query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service):
        result = storage._count_blob_prefix("prefix/", svc=svc)

    assert result == {"would_delete": 3}
    mock_container.list_blobs.assert_called_once_with(name_starts_with="prefix/")


def test_count_blob_prefix_skips_blobs_with_no_name():
    svc = _make_svc()
    blobs = [_make_blob("prefix/a.txt"), _make_blob(""), _make_blob(None)]

    mock_container = MagicMock()
    mock_container.list_blobs.return_value = blobs
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch("query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service):
        result = storage._count_blob_prefix("prefix/", svc=svc)

    assert result == {"would_delete": 1}


def test_count_blob_prefix_returns_zero_on_exception():
    svc = _make_svc()

    mock_container = MagicMock()
    mock_container.list_blobs.side_effect = Exception("network error")
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch("query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service):
        result = storage._count_blob_prefix("prefix/", svc=svc)

    assert result == {"would_delete": 0}
    svc.logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _delete_blob_prefix
# ---------------------------------------------------------------------------


def test_delete_blob_prefix_returns_zero_when_upload_disabled():
    svc = _make_svc(upload_enabled=False)
    result = storage._delete_blob_prefix("prefix/", svc=svc)
    assert result == {"deleted": 0}


def test_delete_blob_prefix_deletes_all_matching_blobs():
    svc = _make_svc()
    blobs = [_make_blob("prefix/a.txt"), _make_blob("prefix/b.txt")]

    mock_container = MagicMock()
    mock_container.list_blobs.return_value = blobs
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch("query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service):
        result = storage._delete_blob_prefix("prefix/", svc=svc)

    assert result == {"deleted": 2}
    assert mock_container.delete_blob.call_count == 2
    mock_container.delete_blob.assert_any_call("prefix/a.txt")
    mock_container.delete_blob.assert_any_call("prefix/b.txt")


def test_delete_blob_prefix_skips_blobs_with_no_name():
    svc = _make_svc()
    blobs = [_make_blob("prefix/a.txt"), _make_blob(""), _make_blob(None)]

    mock_container = MagicMock()
    mock_container.list_blobs.return_value = blobs
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch("query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service):
        result = storage._delete_blob_prefix("prefix/", svc=svc)

    assert result == {"deleted": 1}


def test_delete_blob_prefix_returns_zero_on_exception():
    svc = _make_svc()

    mock_container = MagicMock()
    mock_container.list_blobs.side_effect = Exception("timeout")
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch("query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service):
        result = storage._delete_blob_prefix("prefix/", svc=svc)

    assert result == {"deleted": 0}
    svc.logger.warning.assert_called_once()


def test_delete_blob_prefix_constructs_correct_account_url():
    svc = _make_svc()
    svc.config.storage_account_name = "testaccount"

    mock_container = MagicMock()
    mock_container.list_blobs.return_value = []
    mock_blob_service = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    with patch(
        "query_web.pipeline.storage.BlobServiceClient", return_value=mock_blob_service
    ) as mock_cls:
        storage._delete_blob_prefix("prefix/", svc=svc)

    mock_cls.assert_called_once_with(
        account_url="https://testaccount.blob.core.windows.net",
        credential=svc.credential,
    )


# ---------------------------------------------------------------------------
# _count_blob_prefix / _delete_blob_prefix — AWS (S3)
# ---------------------------------------------------------------------------


def test_count_blob_prefix_returns_zero_when_aws_upload_disabled():
    svc = _make_aws_svc(upload_enabled=False)
    result = storage._count_blob_prefix("corpus-c/by-dedupe/", svc=svc)
    assert result == {"would_delete": 0}


def test_count_blob_prefix_counts_matching_s3_objects():
    svc = _make_aws_svc()
    mock_s3_client = MagicMock()
    mock_s3_client.list_objects.return_value = ["corpus-c/by-dedupe/a", "corpus-c/by-dedupe/b"]

    with patch(
        "runtime.storage.aws_s3.AWSS3StorageClient", return_value=mock_s3_client
    ) as mock_cls:
        result = storage._count_blob_prefix("corpus-c/by-dedupe/", svc=svc)

    assert result == {"would_delete": 2}
    mock_cls.assert_called_once_with(session=svc.credential)
    mock_s3_client.list_objects.assert_called_once_with("my-bucket", prefix="corpus-c/by-dedupe/")


def test_count_blob_prefix_returns_zero_on_s3_exception():
    svc = _make_aws_svc()
    mock_s3_client = MagicMock()
    mock_s3_client.list_objects.side_effect = Exception("network error")

    with patch("runtime.storage.aws_s3.AWSS3StorageClient", return_value=mock_s3_client):
        result = storage._count_blob_prefix("corpus-c/by-dedupe/", svc=svc)

    assert result == {"would_delete": 0}
    svc.logger.warning.assert_called_once()


def test_delete_blob_prefix_returns_zero_when_aws_upload_disabled():
    svc = _make_aws_svc(upload_enabled=False)
    result = storage._delete_blob_prefix("corpus-c/by-dedupe/", svc=svc)
    assert result == {"deleted": 0}


def test_delete_blob_prefix_deletes_all_matching_s3_objects():
    svc = _make_aws_svc()
    mock_s3_client = MagicMock()
    mock_s3_client.list_objects.return_value = ["corpus-c/by-dedupe/a", "corpus-c/by-dedupe/b"]

    with patch("runtime.storage.aws_s3.AWSS3StorageClient", return_value=mock_s3_client):
        result = storage._delete_blob_prefix("corpus-c/by-dedupe/", svc=svc)

    assert result == {"deleted": 2}
    assert mock_s3_client.delete_object.call_count == 2
    mock_s3_client.delete_object.assert_any_call("my-bucket", "corpus-c/by-dedupe/a")
    mock_s3_client.delete_object.assert_any_call("my-bucket", "corpus-c/by-dedupe/b")


def test_delete_blob_prefix_returns_zero_on_s3_exception():
    svc = _make_aws_svc()
    mock_s3_client = MagicMock()
    mock_s3_client.list_objects.side_effect = Exception("timeout")

    with patch("runtime.storage.aws_s3.AWSS3StorageClient", return_value=mock_s3_client):
        result = storage._delete_blob_prefix("corpus-c/by-dedupe/", svc=svc)

    assert result == {"deleted": 0}
    svc.logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _count_blob_prefix / _delete_blob_prefix — local (no blob backend)
# ---------------------------------------------------------------------------


def test_count_blob_prefix_noop_for_local_provider():
    svc = MagicMock()
    svc.config.cloud_provider = "local"
    result = storage._count_blob_prefix("corpus-c/by-dedupe/", svc=svc)
    assert result == {"would_delete": 0}


def test_delete_blob_prefix_noop_for_local_provider():
    svc = MagicMock()
    svc.config.cloud_provider = "local"
    result = storage._delete_blob_prefix("corpus-c/by-dedupe/", svc=svc)
    assert result == {"deleted": 0}
