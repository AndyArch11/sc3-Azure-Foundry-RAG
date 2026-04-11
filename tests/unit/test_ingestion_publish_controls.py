from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from azure.core.credentials import AccessToken
from azure.search.documents import SearchClient

from runtime.ingestion import publish_controls
from runtime.ingestion.controls_index import ControlsIndexConfig


def _record(requirement_id: str = "CTRL-1") -> dict[str, object]:
    return {
        "requirement_id": requirement_id,
        "framework": "FrameworkX",
        "framework_version": "1.0",
        "control_family": "Family",
        "maturity_level": 1,
        "requirement_text": "Do the thing",
        "guidance_text": "Guide",
        "keywords": ["k1"],
        "source_uri": "https://example.com",
        "source_section": "Section",
        "effective_date": "2026",
        "jurisdiction_or_scope": "AU",
    }


class _FakeSearchClient:
    def __init__(self, endpoint: str, index_name: str, credential):
        self.endpoint = endpoint
        self.index_name = index_name
        self.deleted: list[list[dict[str, str]]] = []
        self.uploaded: list[list[dict[str, object]]] = []

    def search(self, **kwargs):
        return []

    def delete_documents(self, docs):
        self.deleted.append(list(docs))

    def merge_or_upload_documents(self, batch):
        self.uploaded.append(list(batch))
        return [SimpleNamespace(succeeded=True) for _ in batch]


class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        return AccessToken("token", 9999999999)


def test_controls_manifest_hash_is_stable_for_ordering() -> None:
    h1 = publish_controls._controls_manifest_hash([_record("A"), _record("B")])
    h2 = publish_controls._controls_manifest_hash([_record("B"), _record("A")])
    assert h1 == h2


def test_framework_version_state_collects_ids_and_manifests() -> None:
    class _Client:
        def search(self, **kwargs):
            return [
                {"requirement_id": "R1", "ingestion_manifest_hash": "mh1"},
                {"requirement_id": "R2", "ingestion_manifest_hash": "mh2"},
            ]

    ids, manifests = publish_controls._framework_version_state(
        cast(SearchClient, _Client()), "FW", "1"
    )
    assert ids == ["R1", "R2"]
    assert manifests == {"mh1", "mh2"}


def test_delete_requirements_batches_calls() -> None:
    class _Client:
        def __init__(self):
            self.calls = 0

        def delete_documents(self, docs):
            self.calls += 1

    client = _Client()
    publish_controls._delete_requirements(cast(SearchClient, client), ["a", "b", "c"], batch_size=2)
    assert client.calls == 2


def test_upload_controls_records_dry_run_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish_controls, "SearchClient", _FakeSearchClient)
    monkeypatch.setattr(
        publish_controls, "_framework_version_state", lambda *args, **kwargs: ([], set())
    )

    cfg = ControlsIndexConfig(
        search_endpoint="https://search.example", controls_index_name="controls"
    )
    summary = publish_controls.upload_controls_records(
        cfg, credential=_FakeCredential(), records=[_record()], dry_run=True
    )

    assert summary["action"] == "would_upload"
    assert summary["records_would_upload"] == 1


def test_upload_controls_records_skips_duplicate_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish_controls, "SearchClient", _FakeSearchClient)

    records = [_record()]
    manifest = publish_controls._controls_manifest_hash(records)
    monkeypatch.setattr(
        publish_controls,
        "_framework_version_state",
        lambda *args, **kwargs: (["CTRL-1"], {manifest}),
    )

    cfg = ControlsIndexConfig(
        search_endpoint="https://search.example", controls_index_name="controls"
    )
    summary = publish_controls.upload_controls_records(
        cfg, credential=_FakeCredential(), records=records
    )

    assert summary["action"] == "skip_duplicate"
    assert summary["records_skipped"] == 1


def test_upload_controls_records_replace_existing_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish_controls, "SearchClient", _FakeSearchClient)
    monkeypatch.setattr(
        publish_controls,
        "_framework_version_state",
        lambda *args, **kwargs: (["CTRL-1"], {"other"}),
    )

    cfg = ControlsIndexConfig(
        search_endpoint="https://search.example", controls_index_name="controls"
    )
    summary = publish_controls.upload_controls_records(
        cfg,
        credential=_FakeCredential(),
        records=[_record()],
        replace_existing=True,
        dry_run=True,
    )

    assert summary["action"] == "would_replace"
    assert summary["records_would_delete"] == 1


def test_upload_controls_records_mixed_framework_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish_controls, "SearchClient", _FakeSearchClient)
    monkeypatch.setattr(
        publish_controls, "_framework_version_state", lambda *args, **kwargs: ([], set())
    )

    mixed = [_record("A"), {**_record("B"), "framework_version": "2.0"}]
    cfg = ControlsIndexConfig(
        search_endpoint="https://search.example", controls_index_name="controls"
    )
    with pytest.raises(ValueError, match="mixed framework/framework_version"):
        publish_controls.upload_controls_records(cfg, credential=_FakeCredential(), records=mixed)


def test_upload_controls_records_uploads_and_enriches(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, _FakeSearchClient] = {}

    class _CaptureClient(_FakeSearchClient):
        def __init__(self, endpoint: str, index_name: str, credential):
            super().__init__(endpoint, index_name, credential)
            holder["client"] = self

    monkeypatch.setattr(publish_controls, "SearchClient", _CaptureClient)
    monkeypatch.setattr(
        publish_controls, "_framework_version_state", lambda *args, **kwargs: ([], set())
    )

    cfg = ControlsIndexConfig(
        search_endpoint="https://search.example", controls_index_name="controls"
    )
    summary = publish_controls.upload_controls_records(
        cfg, credential=_FakeCredential(), records=[_record()], batch_size=1
    )

    assert summary["action"] == "uploaded"
    assert summary["records_uploaded"] == 1
    uploaded_record = holder["client"].uploaded[0][0]
    assert "ingestion_manifest_hash" in uploaded_record
    assert "ingestion_loaded_at" in uploaded_record


def test_load_controls_jsonl_invalid_line_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSONL"):
        publish_controls.load_controls_jsonl(p)
