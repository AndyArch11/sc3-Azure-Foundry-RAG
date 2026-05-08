from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from runtime.ingestion import grounding_index_aws as mod
from runtime.ingestion.grounding_index_aws import AWSGroundingIndexConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        def __init__(self, creds: Any, service: str, region: str) -> None:
            pass

        def add_auth(self, request: _AWSRequest) -> None:
            request.headers["Authorization"] = "FakeSigV4"

    auth_mod.SigV4Auth = _SigV4Auth
    awsreq_mod.AWSRequest = _AWSRequest
    monkeypatch.setitem(sys.modules, "botocore.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "botocore.awsrequest", awsreq_mod)


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")


def _cfg() -> AWSGroundingIndexConfig:
    return AWSGroundingIndexConfig(
        opensearch_endpoint="https://os.example",
        grounding_index_name="grounding-index",
    )


# ---------------------------------------------------------------------------
# AWSGroundingIndexConfig.from_env
# ---------------------------------------------------------------------------


def test_from_env_raises_when_endpoint_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="OPENSEARCH_ENDPOINT"):
        AWSGroundingIndexConfig.from_env()


def test_from_env_uses_default_index_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://os.example")
    monkeypatch.delenv("OPENSEARCH_GROUNDING_INDEX_NAME", raising=False)
    cfg = AWSGroundingIndexConfig.from_env()
    assert cfg.opensearch_endpoint == "https://os.example"
    assert cfg.grounding_index_name == "grounding-index"


def test_from_env_uses_custom_index_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://os.example")
    monkeypatch.setenv("OPENSEARCH_GROUNDING_INDEX_NAME", "my-grounding")
    cfg = AWSGroundingIndexConfig.from_env()
    assert cfg.grounding_index_name == "my-grounding"


# ---------------------------------------------------------------------------
# _signed_headers
# ---------------------------------------------------------------------------


def test_signed_headers_raises_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)
    session = SimpleNamespace(get_credentials=lambda: None, region_name="ap-southeast-2")

    with pytest.raises(RuntimeError, match="Unable to resolve AWS credentials"):
        mod._signed_headers(session, "HEAD", "https://os.example/idx", "")


def test_signed_headers_returns_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)

    class _Creds:
        def get_frozen_credentials(self) -> object:
            return object()

    session = SimpleNamespace(get_credentials=lambda: _Creds(), region_name="ap-southeast-2")
    headers = mod._signed_headers(session, "PUT", "https://os.example/idx", "{}")

    assert headers["Authorization"] == "FakeSigV4"
    assert headers["Content-Type"] == "application/json"


def test_signed_headers_falls_back_to_env_region(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    class _Creds:
        def get_frozen_credentials(self) -> object:
            return object()

    # region_name=None forces env fallback
    session = SimpleNamespace(get_credentials=lambda: _Creds(), region_name=None)
    headers = mod._signed_headers(session, "HEAD", "https://os.example/idx", "")
    assert "Authorization" in headers


# ---------------------------------------------------------------------------
# ensure_grounding_index_aws
# ---------------------------------------------------------------------------


def test_ensure_index_noop_when_index_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})
    calls: list[str] = []

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Resp:
        calls.append(method)
        return _Resp(200)

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)

    mod.ensure_grounding_index_aws(_cfg(), session=SimpleNamespace())

    # Only the HEAD check should be made; no PUT
    assert calls == ["HEAD"]


def test_ensure_index_creates_index_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})
    calls: list[str] = []

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Resp:
        calls.append(method)
        if method == "HEAD":
            return _Resp(404)
        return _Resp(200)

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)

    mod.ensure_grounding_index_aws(_cfg(), session=SimpleNamespace())

    assert calls == ["HEAD", "PUT"]


def test_ensure_index_raises_on_non_404_head_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Resp:
        return _Resp(503)

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)

    with pytest.raises(requests.HTTPError):
        mod.ensure_grounding_index_aws(_cfg(), session=SimpleNamespace())


def test_ensure_index_put_body_contains_required_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Resp:
        if method == "HEAD":
            return _Resp(404)
        captured["url"] = url
        captured["data"] = kwargs.get("data", "")
        return _Resp(200)

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)

    mod.ensure_grounding_index_aws(_cfg(), session=SimpleNamespace())

    assert "grounding-index" in captured["url"]
    for field in ("content", "chunk_id", "dedupe_hash", "source_path", "ingested_at"):
        assert field in captured["data"]


def test_ensure_index_raises_when_put_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Resp:
        if method == "HEAD":
            return _Resp(404)
        return _Resp(500)

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)

    with pytest.raises(requests.HTTPError):
        mod.ensure_grounding_index_aws(_cfg(), session=SimpleNamespace())
