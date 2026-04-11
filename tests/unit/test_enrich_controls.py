from __future__ import annotations

import json

import pytest

from runtime.assessment_orchestration import enrich_controls


def test_enrich_controls_file_enriches_and_skips(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_file = tmp_path / "controls.jsonl"
    output_file = tmp_path / "enriched.jsonl"
    input_file.write_text(
        "\n".join(
            [
                json.dumps({"requirement_id": "R-1", "requirement_text": "alpha"}),
                json.dumps({"requirement_id": "R-2", "control_applicability_scope": "already"}),
                "not-json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def _fake_enrich(control: dict[str, object]) -> dict[str, object]:
        enriched = dict(control)
        enriched["control_applicability_scope"] = "technical"
        return enriched

    monkeypatch.setattr(enrich_controls, "enrich_control_with_applicability", _fake_enrich)

    stats = enrich_controls.enrich_controls_file(
        str(input_file), str(output_file), skip_if_present=True
    )
    out_lines = output_file.read_text(encoding="utf-8").strip().splitlines()

    assert stats == {"enriched": 1, "skipped": 1, "errors": 1, "total": 2}
    assert len(out_lines) == 2
    first = json.loads(out_lines[0])
    second = json.loads(out_lines[1])
    assert first["control_applicability_scope"] == "technical"
    assert second["control_applicability_scope"] == "already"


def test_enrich_controls_main_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        enrich_controls,
        "enrich_controls_file",
        lambda *args, **kwargs: {"enriched": 2, "skipped": 1, "errors": 0, "total": 3},
    )

    exit_code = enrich_controls.main(["input.jsonl", "-o", "out.jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.err)
    assert payload["enriched"] == 2


def test_enrich_controls_main_error_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(enrich_controls, "enrich_controls_file", _boom)

    exit_code = enrich_controls.main(["input.jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: boom" in captured.err
