"""Unit tests for query_web/local_startup.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from query_web.local_startup import _load_local_jsonl_documents, load_local_documents_if_needed

# ---------------------------------------------------------------------------
# _load_local_jsonl_documents — empty / missing paths
# ---------------------------------------------------------------------------


def test_load_empty_path_returns_empty():
    assert _load_local_jsonl_documents("", controls_mode=False) == []


def test_load_whitespace_path_returns_empty():
    assert _load_local_jsonl_documents("   ", controls_mode=False) == []


def test_load_missing_file_returns_empty(tmp_path: Path):
    result = _load_local_jsonl_documents(str(tmp_path / "nonexistent.jsonl"), controls_mode=False)
    assert result == []


# ---------------------------------------------------------------------------
# _load_local_jsonl_documents — evidence mode (controls_mode=False)
# ---------------------------------------------------------------------------


def test_load_evidence_single_file(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    doc = {"content": "hello world", "source_path": "/docs/report.pdf"}
    f.write_text(json.dumps(doc) + "\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert len(result) == 1
    assert result[0]["content"] == "hello world"
    assert result[0]["source_name"] == "report.pdf"


def test_load_evidence_skips_blank_lines(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    doc = {"content": "chunk"}
    f.write_text("\n" + json.dumps(doc) + "\n\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert len(result) == 1


def test_load_evidence_skips_invalid_json(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    f.write_text('{"content": "ok"}\nnot json\n{"content": "also ok"}\n')

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert len(result) == 2


def test_load_evidence_skips_non_dict(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    f.write_text('["not", "a", "dict"]\n{"content": "ok"}\n')

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert len(result) == 1


def test_load_evidence_skips_empty_content(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    f.write_text('{"content": ""}\n{"content": "real"}\n')

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert len(result) == 1
    assert result[0]["content"] == "real"


def test_load_evidence_defaults_corpus_to_b(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    f.write_text(json.dumps({"content": "text"}) + "\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert result[0]["corpus"] == "c"


def test_load_evidence_preserves_existing_corpus(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    f.write_text(json.dumps({"content": "text", "corpus": "a"}) + "\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert result[0]["corpus"] == "a"


def test_load_evidence_uses_chunk_id_as_id(tmp_path: Path):
    f = tmp_path / "chunks.jsonl"
    f.write_text(json.dumps({"content": "text", "chunk_id": "cid-42"}) + "\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=False)
    assert result[0]["id"] == "cid-42"


def test_load_evidence_from_directory_reads_all_jsonl(tmp_path: Path):
    (tmp_path / "a.jsonl").write_text(json.dumps({"content": "first"}) + "\n")
    (tmp_path / "b.jsonl").write_text(json.dumps({"content": "second"}) + "\n")
    (tmp_path / "notes.txt").write_text("ignored")

    result = _load_local_jsonl_documents(str(tmp_path), controls_mode=False)
    assert len(result) == 2
    contents = {r["content"] for r in result}
    assert contents == {"first", "second"}


# ---------------------------------------------------------------------------
# _load_local_jsonl_documents — controls mode (controls_mode=True)
# ---------------------------------------------------------------------------


def test_load_controls_single_record(tmp_path: Path):
    f = tmp_path / "controls.jsonl"
    doc = {
        "requirement_id": "ISM-001",
        "framework": "ISM",
        "framework_version": "2024",
        "requirement_text": "Implement access controls",
    }
    f.write_text(json.dumps(doc) + "\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=True)
    assert len(result) == 1
    assert result[0]["requirement_id"] == "ISM-001"
    assert result[0]["framework"] == "ISM"


def test_load_controls_skips_missing_requirement_text(tmp_path: Path):
    f = tmp_path / "controls.jsonl"
    f.write_text(
        json.dumps({"requirement_id": "A", "framework": "X"})
        + "\n"
        + json.dumps({"requirement_id": "B", "framework": "X", "requirement_text": "Do it"})
        + "\n"
    )

    result = _load_local_jsonl_documents(str(f), controls_mode=True)
    assert len(result) == 1
    assert result[0]["requirement_id"] == "B"


def test_load_controls_search_score_set(tmp_path: Path):
    f = tmp_path / "controls.jsonl"
    f.write_text(json.dumps({"requirement_text": "text", "framework": "F"}) + "\n")

    result = _load_local_jsonl_documents(str(f), controls_mode=True)
    assert result[0]["@search.score"] == 1.0


# ---------------------------------------------------------------------------
# load_local_documents_if_needed
# ---------------------------------------------------------------------------


class _FakeSearchClient:
    def __init__(self) -> None:
        self.loaded: list[list[dict]] = []

    def load_documents(self, docs: list[dict]) -> None:
        self.loaded.append(docs)


class _NoLoadClient:
    """Search client without load_documents — simulates Azure/AWS client."""

    pass


def test_load_documents_noop_for_azure_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "azure")
    sc = _FakeSearchClient()
    cc = _FakeSearchClient()
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]
    assert sc.loaded == []
    assert cc.loaded == []


def test_load_documents_noop_for_aws_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    sc = _FakeSearchClient()
    cc = _FakeSearchClient()
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]
    assert sc.loaded == []


def test_load_documents_noop_for_invalid_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "azuer")
    sc = _FakeSearchClient()
    cc = _FakeSearchClient()
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]
    assert sc.loaded == []
    assert cc.loaded == []


def test_load_documents_calls_load_for_local_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    evidence_file = tmp_path / "chunks.jsonl"
    evidence_file.write_text(json.dumps({"content": "evidence chunk"}) + "\n")
    controls_file = tmp_path / "controls.jsonl"
    controls_file.write_text(
        json.dumps({"requirement_text": "control text", "framework": "F"}) + "\n"
    )

    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EVIDENCE_JSONL_PATH", str(evidence_file))
    monkeypatch.setenv("LOCAL_CONTROLS_JSONL_PATH", str(controls_file))

    sc = _FakeSearchClient()
    cc = _FakeSearchClient()
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]

    assert len(sc.loaded) == 1
    assert sc.loaded[0][0]["content"] == "evidence chunk"
    assert len(cc.loaded) == 1
    assert cc.loaded[0][0]["requirement_text"] == "control text"


def test_load_documents_calls_load_for_dev_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    evidence_file = tmp_path / "chunks.jsonl"
    evidence_file.write_text(json.dumps({"content": "data"}) + "\n")

    monkeypatch.setenv("CLOUD_PROVIDER", "dev")
    monkeypatch.setenv("LOCAL_EVIDENCE_JSONL_PATH", str(evidence_file))
    monkeypatch.setenv("LOCAL_CONTROLS_JSONL_PATH", str(tmp_path / "nonexistent"))

    sc = _FakeSearchClient()
    cc = _FakeSearchClient()
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]

    assert len(sc.loaded) == 1


def test_load_documents_skips_client_without_load_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EVIDENCE_JSONL_PATH", str(tmp_path / "missing.jsonl"))
    monkeypatch.setenv("LOCAL_CONTROLS_JSONL_PATH", str(tmp_path / "missing"))

    sc = _NoLoadClient()
    cc = _NoLoadClient()
    # Should not raise even though neither client has load_documents
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]


def test_load_file_read_exception_is_swallowed(tmp_path: Path):
    """An OSError while opening a file should log a warning and continue."""
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text(json.dumps({"content": "ok"}) + "\n")
    good_file = tmp_path / "good.jsonl"
    good_file.write_text(json.dumps({"content": "good"}) + "\n")

    real_path_open = Path.open

    def patched_open(self, *args, **kwargs):
        if self.name == "bad.jsonl":
            raise OSError("disk error")
        return real_path_open(self, *args, **kwargs)

    with patch("pathlib.Path.open", patched_open):
        result = _load_local_jsonl_documents(str(tmp_path), controls_mode=False)

    # good.jsonl should still be loaded
    assert any(d["content"] == "good" for d in result)


def test_load_documents_handles_exception_gracefully(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EVIDENCE_JSONL_PATH", "irrelevant")
    monkeypatch.setenv("LOCAL_CONTROLS_JSONL_PATH", "irrelevant")

    class _BrokenClient:
        def load_documents(self, docs):
            raise RuntimeError("boom")

    # Should warn and not propagate
    load_local_documents_if_needed(_BrokenClient(), _BrokenClient())  # type: ignore[arg-type]


def test_load_documents_uses_workspace_parsed_controls_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    evidence_file = tmp_path / "chunks.jsonl"
    evidence_file.write_text(json.dumps({"content": "evidence"}) + "\n")

    parsed_controls = tmp_path / "parsed-controls"
    parsed_controls.mkdir()
    (parsed_controls / "controls.jsonl").write_text(
        json.dumps({"requirement_text": "control text", "framework": "ISM"}) + "\n"
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EVIDENCE_JSONL_PATH", str(evidence_file))
    monkeypatch.delenv("LOCAL_CONTROLS_JSONL_PATH", raising=False)

    sc = _FakeSearchClient()
    cc = _FakeSearchClient()
    load_local_documents_if_needed(sc, cc)  # type: ignore[arg-type]

    assert len(cc.loaded) == 1
    assert cc.loaded[0][0]["requirement_text"] == "control text"
