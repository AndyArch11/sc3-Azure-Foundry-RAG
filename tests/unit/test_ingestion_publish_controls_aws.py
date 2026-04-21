from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from runtime.ingestion import publish_controls_aws as mod


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


def _cfg() -> Any:
    return SimpleNamespace(opensearch_endpoint="https://os.example", controls_index_name="controls")


def _record(req_id: str = "CTRL-1", *, fw: str = "ISM", ver: str = "2024") -> dict[str, Any]:
    return {
        "requirement_id": req_id,
        "framework": fw,
        "framework_version": ver,
        "requirement_text": "Implement X",
    }


def test_search_existing_framework_version_returns_empty_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})
    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: _Resp(404))

    ids, manifests = mod._search_existing_framework_version(
        _cfg(),
        session=SimpleNamespace(),
        framework="ISM",
        framework_version="2024",
    )
    assert ids == []
    assert manifests == set()


def test_search_existing_framework_version_parses_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})
    payload = {
        "hits": {
            "hits": [
                {"_source": {"requirement_id": "R1", "ingestion_manifest_hash": "h1"}},
                {"_source": {"requirement_id": "R2", "ingestion_manifest_hash": "h2"}},
                {"_source": {"requirement_id": "", "ingestion_manifest_hash": ""}},
            ]
        }
    }
    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: _Resp(200, payload))

    ids, manifests = mod._search_existing_framework_version(
        _cfg(),
        session=SimpleNamespace(),
        framework="ISM",
        framework_version="2024",
    )
    assert ids == ["R1", "R2"]
    assert manifests == {"h1", "h2"}


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


def test_bulk_delete_requirements_noop_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"post": False}

    def _post(*args, **kwargs):
        called["post"] = True
        return _Resp(200)

    monkeypatch.setattr(mod.requests, "post", _post)
    mod._bulk_delete_requirements(_cfg(), session=SimpleNamespace(), requirement_ids=[])
    assert called["post"] is False


def test_bulk_delete_requirements_posts_newline_delimited_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    captured: dict[str, str] = {}

    def _post(url: str, data: str, headers: dict[str, str], timeout: int):
        captured["url"] = url
        captured["data"] = data
        return _Resp(200)

    monkeypatch.setattr(mod.requests, "post", _post)
    mod._bulk_delete_requirements(_cfg(), session=SimpleNamespace(), requirement_ids=["A", "B"])

    assert captured["url"].endswith("/_bulk?refresh=true")
    assert captured["data"].endswith("\n")
    assert '"_id": "A"' in captured["data"]
    assert '"_id": "B"' in captured["data"]


def test_upload_controls_records_raises_on_mixed_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("A", ver="2024"), _record("B", ver="2025")]
    with pytest.raises(ValueError, match="mixed framework/framework_version"):
        mod.upload_controls_records_aws(_cfg(), session=SimpleNamespace(), records=records)


def test_upload_controls_records_skip_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("A")]
    manifest = mod._controls_manifest_hash(records)
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: (["A"], {manifest})
    )

    out = mod.upload_controls_records_aws(_cfg(), session=SimpleNamespace(), records=records)
    assert out["action"] == "skip_duplicate"
    assert out["records_skipped"] == 1


def test_upload_controls_records_skip_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("A")]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: (["A"], {"other"})
    )

    out = mod.upload_controls_records_aws(_cfg(), session=SimpleNamespace(), records=records)
    assert out["action"] == "skip_conflict"


def test_upload_controls_records_would_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("A")]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: (["A", "B"], {"other"})
    )

    out = mod.upload_controls_records_aws(
        _cfg(),
        session=SimpleNamespace(),
        records=records,
        replace_existing=True,
        dry_run=True,
    )
    assert out["action"] == "would_replace"
    assert out["records_would_delete"] == 2


def test_upload_controls_records_would_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("A")]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: ([], set())
    )

    out = mod.upload_controls_records_aws(
        _cfg(),
        session=SimpleNamespace(),
        records=records,
        dry_run=True,
    )
    assert out["action"] == "would_upload"
    assert out["records_would_upload"] == 1


def test_upload_controls_records_replaces_then_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("A"), _record("B")]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: (["OLD-1"], {"other"})
    )

    deleted_calls: list[list[str]] = []

    def _bulk_delete(config, session, requirement_ids):
        deleted_calls.append(list(requirement_ids))

    monkeypatch.setattr(mod, "_bulk_delete_requirements", _bulk_delete)
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    # Bulk response: 1 success and 1 failure
    bulk_payload = {
        "items": [
            {"index": {"status": 201}},
            {"index": {"status": 500}},
        ]
    }
    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: _Resp(200, bulk_payload))

    out = mod.upload_controls_records_aws(
        _cfg(),
        session=SimpleNamespace(),
        records=records,
        replace_existing=True,
        dry_run=False,
        batch_size=100,
    )

    assert deleted_calls == [["OLD-1"]]
    assert out["action"] == "uploaded"
    assert out["records_uploaded"] == 1
    assert out["records_failed"] == 1


def test_upload_controls_records_missing_requirement_id_counts_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_record("A"), {**_record("B"), "requirement_id": ""}]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: ([], set())
    )
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    bulk_payload = {"items": [{"index": {"status": 200}}]}
    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: _Resp(200, bulk_payload))

    out = mod.upload_controls_records_aws(
        _cfg(),
        session=SimpleNamespace(),
        records=records,
        dry_run=False,
    )
    assert out["records_uploaded"] == 1
    assert out["records_failed"] == 1


def test_upload_controls_records_import_enrichment_failure_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_record("A")]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: ([], set())
    )
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    fake_assessment = types.ModuleType("runtime.assessment_orchestration")

    def _boom(record: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("enrichment unavailable")

    fake_assessment.enrich_control_with_applicability = _boom
    monkeypatch.setitem(sys.modules, "runtime.assessment_orchestration", fake_assessment)
    monkeypatch.setattr(
        mod.requests,
        "post",
        lambda *args, **kwargs: _Resp(200, {"items": [{"index": {"status": 200}}]}),
    )

    out = mod.upload_controls_records_aws(_cfg(), session=SimpleNamespace(), records=records)
    assert out["records_uploaded"] == 1
    assert out["records_failed"] == 0


def test_upload_controls_records_all_missing_ids_skips_bulk_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [{**_record("A"), "requirement_id": ""}, {**_record("B"), "requirement_id": ""}]
    monkeypatch.setattr(
        mod, "_search_existing_framework_version", lambda *args, **kwargs: ([], set())
    )

    called = {"post": 0}

    def _post(*args, **kwargs):
        called["post"] += 1
        return _Resp(200, {"items": []})

    monkeypatch.setattr(mod.requests, "post", _post)
    monkeypatch.setattr(mod, "_signed_headers", lambda *args, **kwargs: {"h": "v"})

    out = mod.upload_controls_records_aws(_cfg(), session=SimpleNamespace(), records=records)
    assert out["records_uploaded"] == 0
    assert out["records_failed"] == 2
    assert called["post"] == 0
