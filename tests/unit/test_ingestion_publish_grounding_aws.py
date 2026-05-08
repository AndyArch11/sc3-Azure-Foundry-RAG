from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from runtime.ingestion import publish_grounding_aws as mod

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


def _cfg() -> Any:
    return SimpleNamespace(
        opensearch_endpoint="https://os.example",
        grounding_index_name="grounding-index",
    )


def _chunk(chunk_id: str = "chunk-1", dedupe_hash: str = "h1") -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "content": "Some content",
        "source_path": "doc.pdf",
        "dedupe_hash": dedupe_hash,
    }


# ---------------------------------------------------------------------------
# _signed_headers
# ---------------------------------------------------------------------------


def test_signed_headers_raises_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)
    session = SimpleNamespace(get_credentials=lambda: None, region_name="ap-southeast-2")

    with pytest.raises(RuntimeError, match="Unable to resolve AWS credentials"):
        mod._signed_headers(session, "POST", "https://os.example/_bulk", "{}")


def test_signed_headers_returns_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_botocore(monkeypatch)

    class _Creds:
        def get_frozen_credentials(self) -> object:
            return object()

    session = SimpleNamespace(get_credentials=lambda: _Creds(), region_name="ap-southeast-2")
    headers = mod._signed_headers(session, "POST", "https://os.example/_bulk", "{}")

    assert headers["Authorization"] == "FakeSigV4"
    assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# _bulk_index_chunks
# ---------------------------------------------------------------------------


def test_bulk_index_chunks_noop_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"post": False}

    def _post(*args: Any, **kwargs: Any) -> _Resp:
        called["post"] = True
        return _Resp(200)

    monkeypatch.setattr(mod, "request_with_instrumentation", _post)
    indexed, failed = mod._bulk_index_chunks(_cfg(), session=SimpleNamespace(), chunks=[])
    assert indexed == 0
    assert failed == 0
    assert called["post"] is False


def test_bulk_index_chunks_skips_chunk_without_id(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"post": False}

    def _post(*args: Any, **kwargs: Any) -> _Resp:
        called["post"] = True
        return _Resp(200)

    monkeypatch.setattr(mod, "request_with_instrumentation", _post)
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})
    # chunk with no chunk_id — should be skipped, producing an empty body and no POST
    indexed, failed = mod._bulk_index_chunks(
        _cfg(), session=SimpleNamespace(), chunks=[{"content": "no id"}]
    )
    assert indexed == 0
    assert failed == 0
    assert called["post"] is False


def test_bulk_index_chunks_counts_indexed_and_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})

    payload = {
        "items": [
            {"index": {"_id": "c1", "status": 201}},
            {"index": {"_id": "c2", "status": 200}},
            {
                "index": {
                    "_id": "c3",
                    "status": 500,
                    "error": {"type": "mapper_exception", "reason": "bad"},
                }
            },
        ]
    }

    monkeypatch.setattr(
        mod,
        "request_with_instrumentation",
        lambda *a, **k: _Resp(200, payload),
    )

    indexed, failed = mod._bulk_index_chunks(
        _cfg(),
        session=SimpleNamespace(),
        chunks=[_chunk("c1"), _chunk("c2"), _chunk("c3", "h3")],
    )
    assert indexed == 2
    assert failed == 1


def test_bulk_index_chunks_posts_newline_delimited_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})
    captured: dict[str, Any] = {}

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Resp:
        captured["url"] = url
        captured["data"] = kwargs.get("data", "")
        return _Resp(200, {"items": [{"index": {"_id": "chunk-1", "status": 200}}]})

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)

    mod._bulk_index_chunks(_cfg(), session=SimpleNamespace(), chunks=[_chunk()])

    assert captured["url"].endswith("/_bulk?refresh=true")
    assert captured["data"].endswith("\n")
    assert "chunk-1" in captured["data"]


# ---------------------------------------------------------------------------
# _fetch_existing_dedupe_hashes
# ---------------------------------------------------------------------------


def test_fetch_existing_dedupe_hashes_returns_empty_when_no_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"post": False}

    def _fake_request(*a: Any, **k: Any) -> _Resp:
        called["post"] = True
        return _Resp(200)

    monkeypatch.setattr(mod, "request_with_instrumentation", _fake_request)
    # chunks with no dedupe_hash — no request should be made
    result = mod._fetch_existing_dedupe_hashes(
        _cfg(), session=SimpleNamespace(), chunks=[{"chunk_id": "c1"}]
    )
    assert result == set()
    assert called["post"] is False


def test_fetch_existing_dedupe_hashes_returns_empty_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})
    monkeypatch.setattr(mod, "request_with_instrumentation", lambda *a, **k: _Resp(404))

    result = mod._fetch_existing_dedupe_hashes(_cfg(), session=SimpleNamespace(), chunks=[_chunk()])
    assert result == set()


def test_fetch_existing_dedupe_hashes_parses_agg_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *a, **k: {"h": "v"})

    payload = {
        "aggregations": {
            "existing_hashes": {
                "buckets": [
                    {"key": "h1", "doc_count": 3},
                    {"key": "h2", "doc_count": 0},  # zero count — should be excluded
                    {"key": "h3", "doc_count": 1},
                ]
            }
        }
    }
    monkeypatch.setattr(mod, "request_with_instrumentation", lambda *a, **k: _Resp(200, payload))

    result = mod._fetch_existing_dedupe_hashes(
        _cfg(),
        session=SimpleNamespace(),
        chunks=[_chunk("c1", "h1"), _chunk("c2", "h2"), _chunk("c3", "h3")],
    )
    assert result == {"h1", "h3"}


# ---------------------------------------------------------------------------
# upload_grounding_chunks_aws
# ---------------------------------------------------------------------------


def test_upload_grounding_chunks_returns_zero_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_fetch_existing_dedupe_hashes", lambda *a, **k: set())
    result = mod.upload_grounding_chunks_aws(_cfg(), session=SimpleNamespace(), chunks=[])
    assert result == {"records_indexed": 0, "records_skipped": 0, "records_failed": 0}


def test_upload_grounding_chunks_skips_already_indexed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_fetch_existing_dedupe_hashes", lambda *a, **k: {"h1"})
    bulk_called = {"n": 0}

    def _fake_bulk(config: Any, session: Any, chunks: list) -> tuple[int, int]:
        bulk_called["n"] += len(chunks)
        return len(chunks), 0

    monkeypatch.setattr(mod, "_bulk_index_chunks", _fake_bulk)

    result = mod.upload_grounding_chunks_aws(
        _cfg(),
        session=SimpleNamespace(),
        chunks=[_chunk("c1", "h1"), _chunk("c2", "h2")],
    )
    assert result["records_skipped"] == 1
    assert result["records_indexed"] == 1
    assert bulk_called["n"] == 1


def test_upload_grounding_chunks_replace_existing_bypasses_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_called = {"n": 0}

    def _fake_fetch(*a: Any, **k: Any) -> set:
        fetch_called["n"] += 1
        return {"h1"}

    monkeypatch.setattr(mod, "_fetch_existing_dedupe_hashes", _fake_fetch)
    monkeypatch.setattr(mod, "_bulk_index_chunks", lambda *a, **k: (1, 0))

    result = mod.upload_grounding_chunks_aws(
        _cfg(),
        session=SimpleNamespace(),
        chunks=[_chunk("c1", "h1")],
        replace_existing=True,
    )
    # With replace_existing=True, dedupe fetch is skipped and all chunks are indexed
    assert fetch_called["n"] == 0
    assert result["records_skipped"] == 0
    assert result["records_indexed"] == 1


def test_upload_grounding_chunks_batches_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_fetch_existing_dedupe_hashes", lambda *a, **k: set())

    batch_sizes: list[int] = []

    def _fake_bulk(config: Any, session: Any, chunks: list) -> tuple[int, int]:
        batch_sizes.append(len(chunks))
        return len(chunks), 0

    monkeypatch.setattr(mod, "_bulk_index_chunks", _fake_bulk)
    # 250 chunks — with _BULK_BATCH_SIZE=100 expect 3 batches: 100, 100, 50
    chunks = [_chunk(f"c{i}", f"h{i}") for i in range(250)]

    result = mod.upload_grounding_chunks_aws(_cfg(), session=SimpleNamespace(), chunks=chunks)

    assert batch_sizes == [100, 100, 50]
    assert result["records_indexed"] == 250
    assert result["records_failed"] == 0


def test_upload_grounding_chunks_accumulates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_fetch_existing_dedupe_hashes", lambda *a, **k: set())
    monkeypatch.setattr(mod, "_bulk_index_chunks", lambda *a, **k: (1, 2))

    result = mod.upload_grounding_chunks_aws(
        _cfg(),
        session=SimpleNamespace(),
        chunks=[_chunk("c1", "h1"), _chunk("c2", "h2"), _chunk("c3", "h3")],
    )
    assert result["records_failed"] == 2
    assert result["records_indexed"] == 1
