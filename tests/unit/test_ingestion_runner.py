from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from runtime.ingestion import runner


def test_run_local_requires_input_dir() -> None:
    args = argparse.Namespace(input_dir=None)
    assert runner._run_local(args) == 2


def test_run_local_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    f = in_dir / "a.pdf"
    f.write_bytes(b"x")
    out = tmp_path / "out" / "chunks.jsonl"

    args = argparse.Namespace(
        input_dir=str(in_dir),
        output_jsonl=str(out),
        chunk_size=100,
        chunk_overlap=10,
        enable_local_ocr=False,
        local_ocr_min_text_chars=80,
    )

    monkeypatch.setattr(runner, "discover_supported_files", lambda path: [f])
    monkeypatch.setattr(
        runner, "extract_source_document", lambda *args, **kwargs: {"source_path": "a.pdf"}
    )
    monkeypatch.setattr(
        runner,
        "chunk_documents",
        lambda docs, chunk_size, chunk_overlap: [
            type(
                "Chunk",
                (),
                {
                    "chunk_id": "c1",
                    "source_path": "a.pdf",
                    "source_type": "pdf",
                    "chunk_index": 0,
                    "content": "hello",
                },
            )()
        ],
    )

    code = runner._run_local(args)
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["chunk_count"] == 1
    assert out.exists()


def test_run_azure_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadCfg:
        @classmethod
        def from_env(cls):
            raise ValueError("bad env")

    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.ingestion.config",
        type("M", (), {"IngestionConfig": _BadCfg}),
    )
    args = argparse.Namespace(skip_upload=True, input_dir=None)
    assert runner._run_azure(args) == 1


def test_run_reset_and_controls_and_main_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Cfg:
        @classmethod
        def from_env(cls):
            return type("C", (), {})()

    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.ingestion.config",
        type("M", (), {"IngestionConfig": _Cfg}),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.ingestion.reset",
        type(
            "R",
            (),
            {"reset_loaded_data": lambda config, credential, purge_blobs=False: {"deleted": 1}},
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": lambda: object()}),
    )

    reset_code = runner._run_reset(argparse.Namespace(purge_blobs=False))
    assert reset_code == 0

    # controls mode
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.ingestion.controls_index",
        type(
            "CI",
            (),
            {
                "ControlsIndexConfig": _Cfg,
                "ensure_controls_index": lambda config, credential: None,
            },
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.ingestion.controls_runner",
        type(
            "CR",
            (),
            {
                "_build_parser_registry": lambda: {
                    "aescsf": {
                        "factory": lambda fetch_guidance: type(
                            "P",
                            (),
                            {
                                "parse": lambda self: [{"id": 1}],
                                "to_jsonl": lambda self, recs: '{"id":1}\n',
                            },
                        )(),
                    }
                },
                "_selected_frameworks": lambda framework, registry: ["aescsf"],
            },
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.ingestion.publish_controls",
        type(
            "PC",
            (),
            {
                "upload_controls_records": lambda *args, **kwargs: {
                    "records_failed": 0,
                    "records_uploaded": 1,
                }
            },
        ),
    )

    controls_args = argparse.Namespace(
        controls_framework="all",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )
    assert runner._run_controls(controls_args) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["mode"] == "controls"

    # main dispatch
    monkeypatch.setattr(runner, "parse_args", lambda: argparse.Namespace(mode="local"))
    monkeypatch.setattr(runner, "_run_local", lambda args: 7)
    assert runner.main() == 7
