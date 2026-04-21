from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest
import requests

from runtime.ingestion import controls_index_aws as mod


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
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_from_env_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="OPENSEARCH_ENDPOINT"):
        mod.AWSControlsIndexConfig.from_env()


def test_from_env_defaults_index_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://os.example")
    monkeypatch.delenv("OPENSEARCH_CONTROLS_INDEX_NAME", raising=False)

    cfg = mod.AWSControlsIndexConfig.from_env()
    assert cfg.opensearch_endpoint == "https://os.example"
    assert cfg.controls_index_name == "controls-index"


def test_from_env_custom_index_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://os.example")
    monkeypatch.setenv("OPENSEARCH_CONTROLS_INDEX_NAME", "my-controls")

    cfg = mod.AWSControlsIndexConfig.from_env()
    assert cfg.controls_index_name == "my-controls"


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


def test_ensure_controls_index_returns_when_index_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = mod.AWSControlsIndexConfig("https://os.example", "controls")
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    head_calls: list[tuple[str, dict]] = []

    def _head(url: str, headers: dict, timeout: int):
        head_calls.append((url, headers))
        return _Resp(200)

    put_called = {"value": False}

    def _put(*args, **kwargs):
        put_called["value"] = True
        return _Resp(200)

    monkeypatch.setattr(mod.requests, "head", _head)
    monkeypatch.setattr(mod.requests, "put", _put)

    mod.ensure_controls_index_aws(cfg, session=SimpleNamespace())

    assert len(head_calls) == 1
    assert put_called["value"] is False


def test_ensure_controls_index_raises_on_unexpected_head_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = mod.AWSControlsIndexConfig("https://os.example", "controls")
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    monkeypatch.setattr(mod.requests, "head", lambda *args, **kwargs: _Resp(500))

    with pytest.raises(requests.HTTPError):
        mod.ensure_controls_index_aws(cfg, session=SimpleNamespace())


def test_ensure_controls_index_creates_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = mod.AWSControlsIndexConfig("https://os.example", "controls")
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})
    monkeypatch.setattr(mod.requests, "head", lambda *args, **kwargs: _Resp(404))

    captured: dict[str, str] = {}

    def _put(url: str, data: str, headers: dict, timeout: int):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers.get("h", "")
        return _Resp(200)

    monkeypatch.setattr(mod.requests, "put", _put)

    mod.ensure_controls_index_aws(cfg, session=SimpleNamespace())

    assert captured["url"].endswith("/controls")
    payload = json.loads(captured["data"])
    assert "mappings" in payload
    assert payload["mappings"]["properties"]["requirement_id"]["type"] == "keyword"
