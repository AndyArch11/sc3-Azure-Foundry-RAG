from __future__ import annotations

from pathlib import Path

import pytest

from runtime.ingestion import controls_runner


class _FakeParser:
    def __init__(self, behaviour: str) -> None:
        self._behaviour = behaviour

    def parse(self):
        if self._behaviour == "missing":
            raise RuntimeError("CIS workbook not found: /tmp/missing.xlsx")
        if self._behaviour == "empty":
            return []
        return [
            {
                "requirement_id": "TEST-001",
                "framework": "Test",
            }
        ]

    def to_jsonl(self, records):
        return "\n".join('{"requirement_id":"%s"}' % r["requirement_id"] for r in records)


@pytest.fixture
def registry_with_optional_and_required(monkeypatch):
    registry = {
        "optional_local": {
            "factory": lambda fetch_guidance: _FakeParser("missing"),
            "output_filename": "optional.jsonl",
            "optional_when_all": True,
        },
        "required_remote": {
            "factory": lambda fetch_guidance: _FakeParser("ok"),
            "output_filename": "required.jsonl",
        },
    }
    monkeypatch.setattr(controls_runner, "_build_parser_registry", lambda: registry)
    return registry


def test_run_parse_all_skips_optional_missing_sources(tmp_path: Path, registry_with_optional_and_required):
    outputs = controls_runner._run_parse(framework="all", output_dir=tmp_path, no_guidance=False)
    assert "required_remote" in outputs
    assert "optional_local" not in outputs
    assert outputs["required_remote"].name == "required.jsonl"


def test_run_parse_single_optional_framework_still_fails(tmp_path: Path, registry_with_optional_and_required):
    with pytest.raises(RuntimeError, match="not found"):
        controls_runner._run_parse(framework="optional_local", output_dir=tmp_path, no_guidance=False)


def test_run_parse_detailed_reports_parsed_and_skipped(tmp_path: Path, registry_with_optional_and_required):
    outputs, skipped = controls_runner._run_parse_detailed(
        framework="all",
        output_dir=tmp_path,
        no_guidance=False,
    )
    assert sorted(outputs.keys()) == ["required_remote"]
    assert skipped == [
        {
            "framework": "optional_local",
            "reason": "CIS workbook not found: /tmp/missing.xlsx",
        }
    ]


def test_run_parse_still_returns_outputs_only(tmp_path: Path, registry_with_optional_and_required):
    outputs = controls_runner._run_parse(framework="all", output_dir=tmp_path, no_guidance=False)
    assert isinstance(outputs, dict)
    assert sorted(outputs.keys()) == ["required_remote"]


def test_framework_all_summary_logs_counts(caplog, tmp_path: Path, registry_with_optional_and_required):
    outputs, skipped = controls_runner._run_parse_detailed(
        framework="all",
        output_dir=tmp_path,
        no_guidance=False,
    )
    with caplog.at_level("INFO"):
        controls_runner._log_framework_all_summary(
            mode="parse",
            parsed_outputs=outputs,
            skipped_frameworks=skipped,
        )
    assert "summary for --framework all" in caplog.text
    assert "parsed=1" in caplog.text
    assert "skipped=1" in caplog.text


def test_missing_source_detector_handles_not_found():
    assert controls_runner._is_missing_source_error(RuntimeError("file not found"))
    assert controls_runner._is_missing_source_error(RuntimeError("No such file or directory"))
    assert not controls_runner._is_missing_source_error(RuntimeError("network timeout"))
