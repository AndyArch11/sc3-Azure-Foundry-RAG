from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from runtime.ingestion.orchestrators import controls_source_orchestrator as cso


def _controls_target_map() -> dict[str, set[str]]:
    return {
        "cis_controls": {
            "CIS_Controls_Version_8.xlsx",
            "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf",
        },
        "pci_dss": {"PCI-DSS-v4_0_1.pdf"},
    }


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeAzureBlob:
    def __init__(self, name: str) -> None:
        self.name = name


def test_is_missing_controls_source_error_marker_matching() -> None:
    assert cso.is_missing_controls_source_error(ValueError("Workbook not found")) is True
    assert cso.is_missing_controls_source_error(ValueError("no such file")) is True
    assert cso.is_missing_controls_source_error(ValueError("network timeout")) is False


def test_download_controls_source_files_azure_empty_prefix_returns_empty() -> None:
    result = cso.download_controls_source_files_azure(
        framework="cis_controls",
        source_prefix=" ",
        credential=object(),
        controls_source_target_filenames=_controls_target_map(),
    )
    assert result == []


def test_download_controls_source_files_azure_unsupported_framework_raises() -> None:
    with pytest.raises(RuntimeError, match="only supported for cis_controls and pci_dss"):
        cso.download_controls_source_files_azure(
            framework="ism",
            source_prefix="x/y",
            credential=object(),
            controls_source_target_filenames=_controls_target_map(),
        )


def test_download_controls_source_files_azure_missing_account_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_STORAGE_ACCOUNT_NAME"):
        cso.download_controls_source_files_azure(
            framework="cis_controls",
            source_prefix="x/y",
            credential=object(),
            controls_source_target_filenames=_controls_target_map(),
        )


def test_download_controls_source_files_azure_success_and_ignores_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "storacct")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER_NAME", "grounding-data")

    writes: dict[str, bytes] = {}
    monkeypatch.setattr(cso.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(
        cso.Path,
        "write_bytes",
        lambda self, data: writes.__setitem__(self.name, data) or len(data),
    )

    expected_files = _controls_target_map()["cis_controls"]
    blob_names = [f"prefix/{name}" for name in sorted(expected_files)] + ["prefix/unexpected.txt"]
    payloads = {name: name.encode("utf-8") for name in blob_names}

    class _Container:
        def list_blobs(self, *, name_starts_with: str):
            assert name_starts_with == "prefix/"
            return [_FakeAzureBlob(name) for name in blob_names]

        def download_blob(self, name: str):
            return SimpleNamespace(readall=lambda: payloads[name])

    class _BlobServiceClient:
        def __init__(self, *, account_url: str, credential: object) -> None:
            assert account_url == "https://storacct.blob.core.windows.net"
            assert credential is not None

        def get_container_client(self, container_name: str):
            assert container_name == "grounding-data"
            return _Container()

    monkeypatch.setitem(
        sys.modules,
        "azure.storage.blob",
        type("BlobMod", (), {"BlobServiceClient": _BlobServiceClient})(),
    )

    result = cso.download_controls_source_files_azure(
        framework="cis_controls",
        source_prefix="prefix",
        credential=object(),
        controls_source_target_filenames=_controls_target_map(),
    )

    assert result == sorted(expected_files)
    assert set(writes.keys()) == expected_files


def test_download_controls_source_files_azure_missing_required_files_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "storacct")
    monkeypatch.setattr(cso.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(cso.Path, "write_bytes", lambda self, data: len(data))

    class _Container:
        def list_blobs(self, *, name_starts_with: str):
            del name_starts_with
            return [_FakeAzureBlob("prefix/CIS_Controls_Version_8.xlsx")]

        def download_blob(self, name: str):
            del name
            return SimpleNamespace(readall=lambda: b"x")

    class _BlobServiceClient:
        def __init__(self, *, account_url: str, credential: object) -> None:
            del account_url, credential

        def get_container_client(self, container_name: str):
            del container_name
            return _Container()

    monkeypatch.setitem(
        sys.modules,
        "azure.storage.blob",
        type("BlobMod", (), {"BlobServiceClient": _BlobServiceClient})(),
    )

    with pytest.raises(RuntimeError, match="Missing staged controls source files"):
        cso.download_controls_source_files_azure(
            framework="cis_controls",
            source_prefix="prefix",
            credential=object(),
            controls_source_target_filenames=_controls_target_map(),
        )


def test_download_controls_source_files_aws_empty_prefix_returns_empty() -> None:
    result = cso.download_controls_source_files_aws(
        framework="cis_controls",
        source_prefix="  ",
        aws_session=object(),
        s3_bucket_name="bucket",
        controls_source_target_filenames=_controls_target_map(),
    )
    assert result == []


def test_download_controls_source_files_aws_missing_bucket_raises() -> None:
    with pytest.raises(RuntimeError, match="S3_BUCKET_NAME"):
        cso.download_controls_source_files_aws(
            framework="cis_controls",
            source_prefix="prefix",
            aws_session=object(),
            s3_bucket_name="",
            controls_source_target_filenames=_controls_target_map(),
        )


def test_download_controls_source_files_aws_unsupported_framework_raises() -> None:
    with pytest.raises(RuntimeError, match="only supported for cis_controls and pci_dss"):
        cso.download_controls_source_files_aws(
            framework="ism",
            source_prefix="prefix",
            aws_session=object(),
            s3_bucket_name="bucket",
            controls_source_target_filenames=_controls_target_map(),
        )


def test_download_controls_source_files_aws_missing_session_client_raises() -> None:
    with pytest.raises(RuntimeError, match="AWS session is not available"):
        cso.download_controls_source_files_aws(
            framework="cis_controls",
            source_prefix="prefix",
            aws_session=SimpleNamespace(),
            s3_bucket_name="bucket",
            controls_source_target_filenames=_controls_target_map(),
        )


def test_download_controls_source_files_aws_success_and_ignores_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: dict[str, bytes] = {}
    monkeypatch.setattr(cso.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(
        cso.Path,
        "write_bytes",
        lambda self, data: writes.__setitem__(self.name, data) or len(data),
    )

    expected_files = _controls_target_map()["pci_dss"]
    keys = ["prefix/PCI-DSS-v4_0_1.pdf", "prefix/notes.txt"]

    class _Paginator:
        def paginate(self, *, Bucket: str, Prefix: str):
            assert Bucket == "bucket"
            assert Prefix == "prefix/"
            return [{"Contents": [{"Key": key} for key in keys]}]

    class _S3Client:
        def get_paginator(self, name: str):
            assert name == "list_objects_v2"
            return _Paginator()

        def get_object(self, *, Bucket: str, Key: str):
            assert Bucket == "bucket"
            return {"Body": _FakeBody(Key.encode("utf-8"))}

    class _Session:
        def client(self, service: str):
            assert service == "s3"
            return _S3Client()

    monkeypatch.setitem(sys.modules, "boto3", type("Boto", (), {"Session": object})())

    result = cso.download_controls_source_files_aws(
        framework="pci_dss",
        source_prefix="prefix",
        aws_session=_Session(),
        s3_bucket_name="bucket",
        controls_source_target_filenames=_controls_target_map(),
    )

    assert result == ["PCI-DSS-v4_0_1.pdf"]
    assert set(writes.keys()) == expected_files


def test_download_controls_source_files_aws_missing_required_files_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cso.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(cso.Path, "write_bytes", lambda self, data: len(data))

    class _Paginator:
        def paginate(self, *, Bucket: str, Prefix: str):
            del Bucket, Prefix
            return [{"Contents": []}]

    class _S3Client:
        def get_paginator(self, name: str):
            del name
            return _Paginator()

        def get_object(self, *, Bucket: str, Key: str):
            del Bucket, Key
            return {"Body": _FakeBody(b"x")}

    class _Session:
        def client(self, service: str):
            del service
            return _S3Client()

    monkeypatch.setitem(sys.modules, "boto3", type("Boto", (), {"Session": object})())

    with pytest.raises(RuntimeError, match="Missing staged controls source files"):
        cso.download_controls_source_files_aws(
            framework="pci_dss",
            source_prefix="prefix",
            aws_session=_Session(),
            s3_bucket_name="bucket",
            controls_source_target_filenames=_controls_target_map(),
        )
