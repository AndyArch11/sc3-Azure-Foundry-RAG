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


def test_run_controls_downloads_staged_source_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Cfg:
        @classmethod
        def from_env(cls):
            return type("C", (), {})()

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
                    "pci_dss": {
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
                "_selected_frameworks": lambda framework, registry: ["pci_dss"],
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
    monkeypatch.setitem(
        __import__("sys").modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": lambda: object()}),
    )

    seen: dict[str, object] = {}

    def _fake_download(framework: str, source_prefix: str, credential: object) -> list[str]:
        seen["framework"] = framework
        seen["source_prefix"] = source_prefix
        seen["credential"] = credential
        return ["PCI-DSS-v4_0_1.pdf"]

    monkeypatch.setattr(runner, "_download_controls_source_files", _fake_download)

    controls_args = argparse.Namespace(
        controls_framework="pci_dss",
        controls_source_prefix="corpus-a/source/pci_dss/batch-123",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )

    assert runner._run_controls(controls_args) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert seen["framework"] == "pci_dss"
    assert seen["source_prefix"] == "corpus-a/source/pci_dss/batch-123"
    assert payload["controls_source_prefix"] == "corpus-a/source/pci_dss/batch-123"
    assert payload["source_files_downloaded"] == ["PCI-DSS-v4_0_1.pdf"]


def test_run_controls_continues_when_one_parser_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Cfg:
        @classmethod
        def from_env(cls):
            return type("C", (), {})()

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
                    "cis_controls": {
                        "factory": lambda fetch_guidance: type(
                            "BadP",
                            (),
                            {
                                "parse": lambda self: (_ for _ in ()).throw(
                                    RuntimeError("CIS workbook not found")
                                ),
                                "to_jsonl": lambda self, recs: "",
                            },
                        )(),
                    },
                    "nist_csf": {
                        "factory": lambda fetch_guidance: type(
                            "GoodP",
                            (),
                            {
                                "parse": lambda self: [{"id": 1}],
                                "to_jsonl": lambda self, recs: '{"id":1}\n',
                            },
                        )(),
                    },
                },
                "_selected_frameworks": lambda framework, registry: ["cis_controls", "nist_csf"],
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
    monkeypatch.setitem(
        __import__("sys").modules,
        "azure.identity",
        type("A", (), {"DefaultAzureCredential": lambda: object()}),
    )

    controls_args = argparse.Namespace(
        controls_framework="all",
        controls_source_prefix="",
        replace_existing=False,
        dry_run=False,
        no_guidance=False,
    )

    exit_code = runner._run_controls(controls_args)
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert exit_code == 1
    results = {item["framework"]: item for item in payload["results"]}
    assert "Parser failed" in results["cis_controls"]["error"]
    assert results["nist_csf"]["records_uploaded"] == 1
