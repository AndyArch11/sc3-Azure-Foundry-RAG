from __future__ import annotations

import json

import pytest

from runtime.assessment_orchestration import azure_assessment_main


def test_azure_assessment_main_emits_json_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        azure_assessment_main,
        "run_azure_assessment",
        lambda **kwargs: {"schema_version": "v1.1", "executive_summary": "ok"},
    )

    exit_code = azure_assessment_main.main(
        ["--subscription-id", "sub-1", "--resource-group", "rg-1"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["result"]["schema_version"] == "v1.1"


def test_azure_assessment_main_returns_nonzero_on_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(**kwargs):
        raise ValueError("broken")

    monkeypatch.setattr(azure_assessment_main, "run_azure_assessment", _boom)

    exit_code = azure_assessment_main.main(
        ["--subscription-id", "sub-1", "--resource-group", "rg-1"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["error"] == "broken"
