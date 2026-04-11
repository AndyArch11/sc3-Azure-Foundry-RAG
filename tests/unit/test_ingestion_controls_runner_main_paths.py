from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from runtime.ingestion import controls_runner


class _FakeParser:
    def __init__(self, records: list[dict[str, object]]):
        self._records = records

    def parse(self):
        return list(self._records)

    def to_jsonl(self, records):
        return "\n".join(json.dumps(r, ensure_ascii=True) for r in records)


def test_selected_frameworks() -> None:
    registry = {"b": {}, "a": {}}
    assert controls_runner._selected_frameworks("all", registry) == ["a", "b"]
    assert controls_runner._selected_frameworks("a", registry) == ["a"]


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["controls-runner"])
    args = controls_runner.parse_args()
    assert args.mode == "parse-and-publish"
    assert args.framework == "essential_eight"


def test_main_ensure_index_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(
        mode="ensure-index",
        framework="essential_eight",
        output_dir="./parsed-controls",
        input_jsonl=None,
        no_guidance=False,
        batch_size=200,
        replace_existing=False,
        dry_run=False,
        log_level="INFO",
        search_endpoint="https://search.example",
        controls_index_name="controls",
    )

    monkeypatch.setattr(controls_runner, "parse_args", lambda: args)
    monkeypatch.setattr(controls_runner, "ensure_controls_index", lambda config, credential: None)
    monkeypatch.setattr(controls_runner, "DefaultAzureCredential", lambda: object())

    code = controls_runner.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["mode"] == "ensure-index"
    assert payload["index_name"] == "controls"


def test_main_parse_mode_single_framework(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    args = argparse.Namespace(
        mode="parse",
        framework="aescsf",
        output_dir=str(tmp_path),
        input_jsonl=None,
        no_guidance=True,
        batch_size=200,
        replace_existing=False,
        dry_run=False,
        log_level="INFO",
        search_endpoint="https://search.example",
        controls_index_name="controls",
    )

    monkeypatch.setattr(controls_runner, "parse_args", lambda: args)
    monkeypatch.setattr(
        controls_runner,
        "_run_parse_detailed",
        lambda framework, output_dir, no_guidance: ({"aescsf": output_dir / "aescsf_v2.jsonl"}, []),
    )

    code = controls_runner.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["mode"] == "parse"
    assert payload["framework"] == "aescsf"
    assert payload["output_jsonl"].endswith("aescsf_v2.jsonl")


def test_main_publish_mode_requires_input(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(
        mode="publish",
        framework="aescsf",
        output_dir="./parsed-controls",
        input_jsonl=None,
        no_guidance=False,
        batch_size=200,
        replace_existing=False,
        dry_run=False,
        log_level="INFO",
        search_endpoint="https://search.example",
        controls_index_name="controls",
    )
    monkeypatch.setattr(controls_runner, "parse_args", lambda: args)
    with pytest.raises(RuntimeError, match="No JSONL source"):
        controls_runner.main()


def test_main_parse_and_publish_all(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    outputs = {
        "aescsf": tmp_path / "aescsf_v2.jsonl",
        "ism": tmp_path / "ism_latest.jsonl",
    }

    args = argparse.Namespace(
        mode="parse-and-publish",
        framework="all",
        output_dir=str(tmp_path),
        input_jsonl=None,
        no_guidance=False,
        batch_size=50,
        replace_existing=True,
        dry_run=True,
        log_level="INFO",
        search_endpoint="https://search.example",
        controls_index_name="controls",
    )

    monkeypatch.setattr(controls_runner, "parse_args", lambda: args)
    monkeypatch.setattr(
        controls_runner,
        "_run_parse_detailed",
        lambda framework, output_dir, no_guidance: (outputs, []),
    )
    monkeypatch.setattr(
        controls_runner,
        "_run_publish",
        lambda *args, **kwargs: {"action": "would_upload", "records_would_upload": 1},
    )

    code = controls_runner.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["mode"] == "parse-and-publish"
    assert payload["framework"] == "all"
    assert sorted(payload["parsed_frameworks"]) == ["aescsf", "ism"]
    assert len(payload["results"]) == 2
