"""Unit tests verifying that outbound trace headers are forwarded by each
runtime HTTP callsite when a scoped trace context is active.

Each test:
1. Activates a scoped_trace_context with a known correlation_id.
2. Patches the relevant transport (requests / urllib.request.urlopen).
3. Calls the production function under test.
4. Asserts that the captured request carried the expected x-correlation-id header.
"""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.trace_context import scoped_trace_context

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CORR_ID = "test-corr-propagation-1"
_TRACEPARENT = "00-aabbccddeeff00112233445566778899-0011223344556677-01"


class _FakeResp:
    """Minimal requests.Response substitute."""

    def __init__(self, status_code: int = 200, payload: Any = None, content: bytes = b"") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = str(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHTTPError(f"http {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeHTTPError(Exception):
    pass


def _fake_requests_module(
    *,
    get_resp: _FakeResp | None = None,
    post_resp: _FakeResp | None = None,
    captured: list[dict[str, Any]] | None = None,
) -> types.SimpleNamespace:
    """Return a fake ``requests`` namespace that records call kwargs."""
    captured_calls: list[dict[str, Any]] = captured if captured is not None else []

    def _get(url, **kwargs):
        captured_calls.append({"method": "GET", "url": url, **kwargs})
        return get_resp or _FakeResp()

    def _post(url, **kwargs):
        captured_calls.append({"method": "POST", "url": url, **kwargs})
        return post_resp or _FakeResp()

    return types.SimpleNamespace(
        get=_get,
        post=_post,
        HTTPError=_FakeHTTPError,
        exceptions=types.SimpleNamespace(RequestException=Exception),
    )


# ---------------------------------------------------------------------------
# local_qdrant.LocalQdrantSearchClient._embed_text
# ---------------------------------------------------------------------------


def _make_qdrant_client(ollama_base_url: str = "http://ollama.local:11434"):
    """Build a LocalQdrantSearchClient with QdrantClient stubbed out."""
    from unittest.mock import MagicMock, patch

    with patch("runtime.search.local_qdrant.LocalQdrantSearchClient.__init__") as _init:
        _init.return_value = None
        from runtime.search.local_qdrant import LocalQdrantSearchClient

        client = LocalQdrantSearchClient.__new__(LocalQdrantSearchClient)
        client._index = "test"
        client._qdrant_url = "http://localhost:6333"
        client._ollama_base_url = ollama_base_url
        client._embedding_model = "nomic-embed-text"
        client._docs = []
        client._client = MagicMock()

    return client


def test_local_qdrant_embed_text_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.search import local_qdrant

    calls: list[dict[str, Any]] = []
    fake_requests = _fake_requests_module(
        post_resp=_FakeResp(payload={"embedding": [0.1, 0.2]}),
        captured=calls,
    )
    monkeypatch.setattr(local_qdrant, "requests", fake_requests)

    client = _make_qdrant_client()

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        client._embed_text("hello world")

    assert calls, "requests.post was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# runtime_wiring._resolve_cloud_id
# ---------------------------------------------------------------------------


def test_runtime_wiring_resolve_cloud_id_propagates_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.assessment_orchestration import runtime_wiring

    calls: list[dict[str, Any]] = []
    fake_requests = _fake_requests_module(
        get_resp=_FakeResp(payload={"cloudId": "test-cloud-id"}),
        captured=calls,
    )
    monkeypatch.setattr(runtime_wiring, "requests", fake_requests)

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        cloud_id = runtime_wiring._resolve_cloud_id("https://example.atlassian.net")

    assert cloud_id == "test-cloud-id"
    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# publish_controls_aws._search_existing_framework_version
# ---------------------------------------------------------------------------


def test_publish_controls_aws_search_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from runtime.ingestion import publish_controls_aws as mod

    calls: list[dict[str, Any]] = []
    fake_requests = _fake_requests_module(
        post_resp=_FakeResp(
            payload={
                "hits": {"hits": [{"_id": "id1", "_source": {"ingestion_manifest_hash": "h1"}}]}
            }
        ),
        captured=calls,
    )
    monkeypatch.setattr(mod, "requests", fake_requests)
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **kw: {"Authorization": "FakeSig"})

    cfg = SimpleNamespace(opensearch_endpoint="https://os.example", controls_index_name="controls")

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        ids, manifests = mod._search_existing_framework_version(
            cfg,
            session=SimpleNamespace(),
            framework="ISM",
            framework_version="2024",
        )

    assert calls, "requests.post was not called"
    # Headers are passed as the prepared dict, not via the requests kwarg, so
    # check the kwargs key directly.
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# pspf._download_pdf_bytes
# ---------------------------------------------------------------------------


def test_pspf_download_pdf_bytes_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.ingestion.parsers import pspf

    calls: list[dict[str, Any]] = []
    fake_requests = _fake_requests_module(
        get_resp=_FakeResp(content=b"%PDF-1.4 test"),
        captured=calls,
    )
    monkeypatch.setattr(pspf, "requests", fake_requests)

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        pspf._download_pdf_bytes("https://example.gov.au/pspf.pdf")

    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# aescsf.AescsfParser._fetch_workbook
# ---------------------------------------------------------------------------


def test_aescsf_parser_fetch_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.ingestion.parsers import aescsf

    calls: list[dict[str, Any]] = []
    fake_requests_mod = _fake_requests_module(
        get_resp=_FakeResp(content=b"fake-workbook-bytes"),
        captured=calls,
    )
    monkeypatch.setattr(aescsf, "requests", fake_requests_mod)

    parser = aescsf.AescsfParser(toolkit_url="https://example.com/aescsf.xlsx")

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        raw = parser._fetch_workbook()

    assert raw == b"fake-workbook-bytes"
    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# ism.IsmParser._fetch_catalog
# ---------------------------------------------------------------------------


def test_ism_parser_fetch_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.ingestion.parsers import ism

    calls: list[dict[str, Any]] = []
    catalog_payload = {"catalog": {"metadata": {}, "groups": []}}

    fake_requests_mod = _fake_requests_module(
        get_resp=_FakeResp(payload=catalog_payload),
        captured=calls,
    )
    monkeypatch.setattr(ism, "requests", fake_requests_mod)

    parser = ism.IsmParser(catalog_url="https://example.com/ism.json")

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        catalog = parser._fetch_catalog()

    assert catalog == catalog_payload
    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# nist_csf._fetch_guidance (local import — patch via sys.modules)
# ---------------------------------------------------------------------------


def test_nist_csf_fetch_guidance_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _FakeSoup:
        def find_all(self, *a, **kw):
            return []

    fake_bs4 = types.ModuleType("bs4")
    fake_bs4.BeautifulSoup = lambda *a, **kw: _FakeSoup()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bs4", fake_bs4)

    fake_requests_mod = _fake_requests_module(
        get_resp=_FakeResp(content=b"<html>NIST</html>"),
        captured=calls,
    )

    from runtime.ingestion.parsers import nist_csf

    monkeypatch.setattr(nist_csf, "requests", fake_requests_mod)

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        nist_csf._fetch_guidance("GV.OC-01")

    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# essential_eight._fetch_soup
# ---------------------------------------------------------------------------


def test_essential_eight_fetch_soup_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _FakeSoup:
        text = ""

        def find_all(self, *a, **kw):
            return []

    fake_bs4 = types.ModuleType("bs4")
    fake_bs4.BeautifulSoup = lambda *a, **kw: _FakeSoup()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bs4", fake_bs4)

    fake_requests_mod = _fake_requests_module(
        get_resp=_FakeResp(content=b"<html>E8</html>"),
        captured=calls,
    )

    from runtime.ingestion.parsers import essential_eight

    monkeypatch.setattr(essential_eight, "requests", fake_requests_mod)

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        essential_eight._fetch_soup("https://www.cyber.gov.au/e8")

    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# nist_ai_rmf._load_pdf_reader (top-level requests import)
# ---------------------------------------------------------------------------


def test_nist_ai_rmf_load_pdf_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.ingestion.parsers import nist_ai_rmf

    calls: list[dict[str, Any]] = []

    # Simulate no local PDF file so the network branch is taken
    monkeypatch.setattr(nist_ai_rmf, "_DEFAULT_PDF_PATH", MagicMock(exists=lambda: False))

    class _FakePdfReader:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(nist_ai_rmf, "_PdfReader", _FakePdfReader)

    fake_requests = _fake_requests_module(
        get_resp=_FakeResp(content=b"%PDF-1.4 fake"),
        captured=calls,
    )
    monkeypatch.setattr(nist_ai_rmf, "requests", fake_requests)

    parser = nist_ai_rmf.NistAiRmfParser()
    parser.pdf_path = None  # force network path

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        parser._load_pdf_reader()

    assert calls, "requests.get was not called"
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# ollama_client — is_ollama_available (requests.get)
# ---------------------------------------------------------------------------


def test_ollama_client_availability_check_propagates_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.assessment_orchestration import ollama_client

    calls: list[dict[str, Any]] = []

    import types as _types

    fake_requests = _types.SimpleNamespace(
        get=lambda url, **kwargs: (calls.append({"url": url, **kwargs}) or _FakeResp(200)),
        post=lambda *a, **kw: _FakeResp(200),
        exceptions=_types.SimpleNamespace(RequestException=Exception),
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        result = ollama_client.is_ollama_available("http://ollama.local:11434")

    assert result is True
    assert calls, "requests.get was not called"
    # headers are only injected when non-empty
    headers = calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# ollama_client — ollama_embedding (requests.post)
# ---------------------------------------------------------------------------


def test_ollama_client_embedding_propagates_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.assessment_orchestration import ollama_client

    post_calls: list[dict[str, Any]] = []

    import types as _types

    fake_requests = _types.SimpleNamespace(
        get=lambda url, **kwargs: _FakeResp(200, {}),
        post=lambda url, **kwargs: (
            post_calls.append({"url": url, **kwargs})
            or _FakeResp(200, {"embeddings": [[0.1, 0.2]]})
        ),
        exceptions=_types.SimpleNamespace(RequestException=Exception),
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    # availability check uses requests.get; make is_ollama_available return True
    monkeypatch.setattr(ollama_client, "is_ollama_available", lambda url: True)

    with scoped_trace_context(correlation_id=_CORR_ID, traceparent=_TRACEPARENT):
        result = ollama_client.ollama_embedding(
            "hello",
            model="nomic-embed-text",
            base_url="http://ollama.local:11434",
        )

    assert result == [0.1, 0.2]
    assert post_calls, "requests.post was not called"
    headers = post_calls[0].get("headers", {})
    assert headers.get("x-correlation-id") == _CORR_ID
    assert headers.get("traceparent") == _TRACEPARENT


# ---------------------------------------------------------------------------
# No context — headers must be absent (zero-overhead guard)
# ---------------------------------------------------------------------------


def test_no_trace_context_produces_no_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify callsites forward nothing when no context is active."""
    from runtime.search import local_qdrant

    calls: list[dict[str, Any]] = []
    fake_requests = _fake_requests_module(
        post_resp=_FakeResp(payload={"embedding": [0.5]}),
        captured=calls,
    )
    monkeypatch.setattr(local_qdrant, "requests", fake_requests)

    client = _make_qdrant_client()
    # Deliberately no scoped_trace_context
    client._embed_text("ping")

    assert calls
    headers = calls[0].get("headers", {})
    assert "x-correlation-id" not in headers
    assert "traceparent" not in headers
