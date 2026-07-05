from __future__ import annotations

from pathlib import Path

from runtime.ingestion.parsers import runner


class _FakeParser:
    def __init__(self, records):
        self._records = records

    def parse(self):
        return list(self._records)

    def to_jsonl(self, records):
        return "\n".join('{"requirement_id":"%s"}' % r["requirement_id"] for r in records)


def test_parse_args_supports_no_guidance_and_output_dir() -> None:
    args = runner._parse_args(["--framework", "aescsf", "--output-dir", "./tmp", "--no-guidance"])
    assert args.framework == "aescsf"
    assert args.output_dir == "./tmp"
    assert args.no_guidance is True


def test_parse_args_accepts_cis_and_pci_path_overrides() -> None:
    args = runner._parse_args(
        [
            "--framework",
            "cis_controls",
            "--cis-workbook-path",
            "./cis.xlsx",
            "--cis-pdf-path",
            "./cis.pdf",
            "--pci-pdf-path",
            "./pci.pdf",
        ]
    )
    assert args.cis_workbook_path == "./cis.xlsx"
    assert args.cis_pdf_path == "./cis.pdf"
    assert args.pci_pdf_path == "./pci.pdf"


def test_build_registry_includes_all_available_framework_parsers() -> None:
    registry = runner._build_registry()
    assert set(registry.keys()) == {
        "aescsf",
        "cis_controls",
        "essential_eight",
        "ism",
        "nist_ai_rmf",
        "nist_csf",
        "pci_dss",
        "pspf",
    }


def test_main_writes_output_and_returns_zero(tmp_path: Path, monkeypatch) -> None:
    registry = {
        "aescsf": {
            "factory": lambda fetch_guidance: _FakeParser([{"requirement_id": "R-1"}]),
            "output_filename": "aescsf_v2.jsonl",
            "description": "desc",
        }
    }
    monkeypatch.setattr(runner, "_build_registry", lambda: registry)

    code = runner.main(["--framework", "aescsf", "--output-dir", str(tmp_path)])
    output_path = tmp_path / "aescsf_v2.jsonl"

    assert code == 0
    assert output_path.exists()
    assert "R-1" in output_path.read_text(encoding="utf-8")


def test_main_returns_one_when_parser_fails(tmp_path: Path, monkeypatch) -> None:
    class _FailingParser:
        def parse(self):
            raise RuntimeError("boom")

    registry = {
        "aescsf": {
            "factory": lambda fetch_guidance: _FailingParser(),
            "output_filename": "aescsf_v2.jsonl",
            "description": "desc",
        }
    }
    monkeypatch.setattr(runner, "_build_registry", lambda: registry)

    assert runner.main(["--framework", "aescsf", "--output-dir", str(tmp_path)]) == 1


def test_main_returns_one_when_no_records(tmp_path: Path, monkeypatch) -> None:
    registry = {
        "aescsf": {
            "factory": lambda fetch_guidance: _FakeParser([]),
            "output_filename": "aescsf_v2.jsonl",
            "description": "desc",
        }
    }
    monkeypatch.setattr(runner, "_build_registry", lambda: registry)

    assert runner.main(["--framework", "aescsf", "--output-dir", str(tmp_path)]) == 1


def test_main_passes_cis_path_overrides_to_factory(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _factory(*, fetch_guidance: bool, **kwargs):
        seen["fetch_guidance"] = fetch_guidance
        seen["kwargs"] = kwargs
        return _FakeParser([{"requirement_id": "R-1"}])

    registry = {
        "cis_controls": {
            "factory": _factory,
            "output_filename": "cis_controls_v8.jsonl",
            "description": "desc",
        }
    }
    monkeypatch.setattr(runner, "_build_registry", lambda: registry)

    code = runner.main(
        [
            "--framework",
            "cis_controls",
            "--output-dir",
            str(tmp_path),
            "--cis-workbook-path",
            "/tmp/cis-workbook.xlsx",
            "--cis-pdf-path",
            "/tmp/cis-controls.pdf",
            "--no-guidance",
        ]
    )

    assert code == 0
    assert seen["fetch_guidance"] is False
    assert seen["kwargs"] == {
        "workbook_path": "/tmp/cis-workbook.xlsx",
        "pdf_path": "/tmp/cis-controls.pdf",
    }
