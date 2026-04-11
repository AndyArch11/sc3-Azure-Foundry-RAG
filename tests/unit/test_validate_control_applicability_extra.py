from __future__ import annotations

import json

import pytest

from runtime.assessment_orchestration import validate_control_applicability as vca


def test_extract_json_object_with_wrapped_text() -> None:
    payload = vca._extract_json_object(
        'result: {"scope":"mixed","confidence":0.7,"rationale":"ok"} trailing'
    )
    assert payload["scope"] == "mixed"


def test_extract_json_object_raises_without_object() -> None:
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        vca._extract_json_object("no object here")


def test_extract_json_object_raises_when_json_not_object() -> None:
    with pytest.raises(ValueError, match="JSON must be an object"):
        vca._extract_json_object("[]")


def test_review_control_with_llm_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def _chat(_messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"
        return '{"scope":"technical","confidence":0.9,"rationale":"clear technical control"}'

    result = vca._review_control_with_llm(
        {"requirement_id": "R-1", "framework": "NIST CSF", "requirement_text": "Enable MFA"},
        heuristic_scope="technical",
        heuristic_confidence=0.8,
        chat_completion=_chat,
    )
    assert calls["n"] == 2
    assert result["scope"] == "technical"


def test_review_ambiguous_controls_with_llm_records_errors() -> None:
    controls = [
        {
            "requirement_id": "X-1",
            "framework": "Test",
            "control_family": "Ops",
            "requirement_text": "Implement encryption policy and procedures",
            "guidance_text": "Configuration and governance workflow",
        }
    ]

    result = vca.review_ambiguous_controls_with_llm(
        controls,
        confidence_threshold=0.99,
        chat_completion=lambda _messages: "{bad json",
    )

    assert result["requested_reviews"] == 1
    assert result["reviewed_controls"] == 0
    assert len(result["errors"]) == 1


def test_review_control_with_llm_rejects_invalid_scope_confidence_and_rationale() -> None:
    control = {"requirement_id": "R-2", "framework": "Test", "requirement_text": "text"}

    with pytest.raises(ValueError, match="Unsupported LLM applicability scope"):
        vca._review_control_with_llm(
            control,
            heuristic_scope="technical",
            heuristic_confidence=0.7,
            chat_completion=lambda _messages: '{"scope":"invalid","confidence":0.8,"rationale":"x"}',
            max_attempts=1,
        )

    with pytest.raises(ValueError, match="Invalid LLM confidence"):
        vca._review_control_with_llm(
            control,
            heuristic_scope="technical",
            heuristic_confidence=0.7,
            chat_completion=lambda _messages: '{"scope":"technical","confidence":"not-a-number","rationale":"x"}',
            max_attempts=1,
        )

    with pytest.raises(ValueError, match="rationale was empty"):
        vca._review_control_with_llm(
            control,
            heuristic_scope="technical",
            heuristic_confidence=0.7,
            chat_completion=lambda _messages: '{"scope":"technical","confidence":0.8,"rationale":""}',
            max_attempts=1,
        )


def test_review_ambiguous_controls_with_llm_uses_default_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = [
        {
            "requirement_id": "R-3",
            "framework": "Test",
            "control_family": "Ops",
            "requirement_text": "policy and procedure with configuration",
            "guidance_text": "mixed control",
        }
    ]

    monkeypatch.setattr(
        vca,
        "create_chat_completion_fn",
        lambda: (lambda _messages: '{"scope":"mixed","confidence":0.8,"rationale":"ok"}'),
    )
    result = vca.review_ambiguous_controls_with_llm(controls, confidence_threshold=0.99)
    assert result["reviewed_controls"] == 1


def test_validate_controls_applicability_discovers_local_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    controls_file = tmp_path / "sample.jsonl"
    controls_file.write_text(
        json.dumps(
            {
                "requirement_id": "R-1",
                "framework": "Test",
                "control_family": "Ops",
                "requirement_text": "Enable MFA and restrict privileged access.",
                "guidance_text": "Technical implementation details.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(vca.glob, "glob", lambda _pattern: [str(controls_file), ".gitignore"])

    result = vca.validate_controls_applicability(
        controls_source=None,
        confidence_threshold=0.5,
        max_results=10,
    )

    assert result["total_controls_classified"] == 1
    assert "scope_distribution" in result


def test_validate_controls_applicability_handles_classification_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    controls_file = tmp_path / "sample.jsonl"
    controls_file.write_text(
        "\n".join(
            [
                '{"requirement_id":"OK-1","framework":"Test","control_family":"Ops","requirement_text":"Enable MFA.","guidance_text":"Technical."}',
                '{"requirement_id":"BAD-1","framework":"Test","control_family":"Ops","requirement_text":"bad","guidance_text":"bad"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    original = vca.classify_control_applicability

    def _classify(control):
        if control.get("requirement_id") == "BAD-1":
            raise RuntimeError("boom")
        return original(control)

    monkeypatch.setattr(vca, "classify_control_applicability", _classify)

    result = vca.validate_controls_applicability(
        controls_source=str(controls_file),
        confidence_threshold=0.5,
        max_results=10,
    )
    assert result["total_controls_classified"] == 1
