from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest
import requests

from runtime.ingestion import reset_aws as mod


def _install_fake_botocore(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_mod = types.ModuleType("botocore.auth")
    awsreq_mod = types.ModuleType("botocore.awsrequest")

    class _AWSRequest:
        def __init__(self, method: str, url: str, data: str, headers: dict[str, str]) -> None:
            self.method = method
            self.url = url
            self.data = data
            self.headers = dict(headers)

    class _SigV4Auth:
        def __init__(self, creds, service: str, region: str) -> None:
            self.creds = creds
            self.service = service
            self.region = region

        def add_auth(self, request: _AWSRequest) -> None:
            request.headers["Authorization"] = "FakeSigV4"

    auth_mod.SigV4Auth = _SigV4Auth
    awsreq_mod.AWSRequest = _AWSRequest
    monkeypatch.setitem(sys.modules, "botocore.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "botocore.awsrequest", awsreq_mod)


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _Storage:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.deleted: list[tuple[str, str]] = []

    def list_objects(self, bucket: str, prefix: str | None = None):
        return list(self.keys)

    def delete_object(self, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


def _cfg(prefix: str | None = "uploads/") -> mod.AWSResetConfig:
    return mod.AWSResetConfig(
        opensearch_endpoint="https://os.example",
        opensearch_index_name="grounding-index",
        s3_bucket_name="my-bucket",
        s3_prefix=prefix,
    )


def test_from_env_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "bucket")

    with pytest.raises(ValueError, match="OPENSEARCH_ENDPOINT"):
        mod.AWSResetConfig.from_env()


def test_from_env_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://os.example")
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)

    with pytest.raises(ValueError, match="AWS_S3_BUCKET_NAME"):
        mod.AWSResetConfig.from_env()


def test_from_env_uses_defaults_and_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://os.example")
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "bucket")
    monkeypatch.setenv("AWS_S3_PREFIX", "docs/")
    monkeypatch.delenv("OPENSEARCH_INDEX", raising=False)
    monkeypatch.delenv("OPENSEARCH_INDEX_NAME", raising=False)

    cfg = mod.AWSResetConfig.from_env()
    assert cfg.opensearch_index_name == "grounding-index"
    assert cfg.s3_prefix == "docs/"


def test_signed_headers_raises_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)
    session = SimpleNamespace(get_credentials=lambda: None, region_name="ap-southeast-2")

    with pytest.raises(RuntimeError, match="Unable to resolve AWS credentials"):
        mod._signed_headers(session, "POST", "https://os.example/_search", "{}")


def test_signed_headers_returns_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)

    class _Creds:
        def get_frozen_credentials(self):
            return object()

    session = SimpleNamespace(get_credentials=lambda: _Creds(), region_name="ap-southeast-2")
    headers = mod._signed_headers(session, "POST", "https://os.example/_search", "{}")

    assert headers["Authorization"] == "FakeSigV4"
    assert headers["Content-Type"] == "application/json"


def test_delete_index_documents_returns_zero_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})
    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: _Resp(404))

    deleted = mod._delete_index_documents(_cfg(), session=SimpleNamespace())
    assert deleted == 0


def test_delete_index_documents_wraps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    def _post(*args, **kwargs):
        return _Resp(500, text="boom")

    monkeypatch.setattr(mod.requests, "post", _post)

    with pytest.raises(RuntimeError, match="Failed to purge OpenSearch"):
        mod._delete_index_documents(_cfg(), session=SimpleNamespace())


def test_delete_index_documents_returns_deleted_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})
    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: _Resp(200, {"deleted": 7}))

    deleted = mod._delete_index_documents(_cfg(), session=SimpleNamespace())
    assert deleted == 7


def test_purge_s3_objects_deletes_all_keys() -> None:
    storage = _Storage(["a", "b", "c"])
    deleted = mod._purge_s3_objects(_cfg(), storage)

    assert deleted == 3
    assert storage.deleted == [
        ("my-bucket", "a"),
        ("my-bucket", "b"),
        ("my-bucket", "c"),
    ]


def test_reset_loaded_data_without_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_delete_index_documents", lambda *args, **kwargs: 5)
    storage = _Storage(["a"])

    out = mod.reset_loaded_data_aws(
        _cfg(),
        session=SimpleNamespace(),
        storage_client=storage,
        purge_objects=False,
    )

    assert out["deleted_index_documents"] == 5
    assert out["deleted_source_objects"] == 0


def test_reset_loaded_data_with_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_delete_index_documents", lambda *args, **kwargs: 2)
    storage = _Storage(["a", "b"])

    out = mod.reset_loaded_data_aws(
        _cfg(),
        session=SimpleNamespace(),
        storage_client=storage,
        purge_objects=True,
    )

    assert out["deleted_index_documents"] == 2
    assert out["deleted_source_objects"] == 2
    assert out["search_index"] == "grounding-index"
