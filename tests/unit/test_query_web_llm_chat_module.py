"""Unit tests for query_web/llm_chat.py — LLM helpers and JSON parsers."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.llm_chat import (
    _json_fallback_eval,
    _parse_eval,
    _parse_validator_response,
    _is_temperature_unsupported_error,
    _prompt_injection_response,
    _chat_completion_with_empty_retry,
    _evaluate,
    _call_validator,
)

# ---------------------------------------------------------------------------
# _json_fallback_eval
# ---------------------------------------------------------------------------

def test_json_fallback_eval_returns_expected_structure() -> None:
    result = _json_fallback_eval()
    assert result["acceptable"] is False
    assert result["score"] == 0.0
    assert "valid JSON" in result["reason"]


# ---------------------------------------------------------------------------
# _prompt_injection_response
# ---------------------------------------------------------------------------

def test_prompt_injection_response_structure() -> None:
    result = _prompt_injection_response("Detected injection attempt")
    assert result["results"] == []
    assert result["controls_results"] == []
    assert result["iterations"] == 1
    assert result["evaluation"]["acceptable"] is False
    assert result["evaluation"]["score"] == 0.0
    assert "Detected injection attempt" in result["evaluation"]["reason"]
    assert result["metrics"]["guardrail_blocked"] == 1.0
    assert result["metrics"]["total_s"] == 0.0


# ---------------------------------------------------------------------------
# _parse_eval
# ---------------------------------------------------------------------------

def test_parse_eval_raw_valid_json() -> None:
    text = '{"acceptable": true, "score": 0.9, "reason": "Well grounded."}'
    result = _parse_eval(text)
    assert result["acceptable"] is True
    assert result["score"] == 0.9
    assert result["reason"] == "Well grounded."


def test_parse_eval_fenced_json() -> None:
    text = '```json\n{"acceptable": false, "score": 0.2, "reason": "Weak evidence."}\n```'
    result = _parse_eval(text)
    assert result["acceptable"] is False
    assert result["score"] == 0.2


def test_parse_eval_json_embedded_in_prose() -> None:
    text = 'Here is my evaluation: {"acceptable": true, "score": 0.7, "reason": "OK"} end.'
    result = _parse_eval(text)
    assert result["acceptable"] is True
    assert result["score"] == 0.7


def test_parse_eval_score_clamped_to_range() -> None:
    text = '{"acceptable": true, "score": 1.5, "reason": "Overflow score."}'
    result = _parse_eval(text)
    assert result["score"] == 1.0


def test_parse_eval_score_clamped_below_zero() -> None:
    text = '{"acceptable": false, "score": -0.5, "reason": "Negative score."}'
    result = _parse_eval(text)
    assert result["score"] == 0.0


def test_parse_eval_missing_required_keys_falls_back() -> None:
    text = '{"mode": "compliance-report"}'
    result = _parse_eval(text)
    assert result == _json_fallback_eval()


def test_parse_eval_invalid_json_falls_back() -> None:
    result = _parse_eval("this is not json at all")
    assert result == _json_fallback_eval()


def test_parse_eval_empty_string_falls_back() -> None:
    result = _parse_eval("")
    assert result == _json_fallback_eval()


def test_parse_eval_default_reason_when_missing() -> None:
    text = '{"acceptable": true, "score": 0.5}'
    result = _parse_eval(text)
    assert result["reason"] == "No reason provided."


def test_parse_eval_only_score_key_is_sufficient() -> None:
    text = '{"score": 0.6, "reason": "Partial grounding."}'
    result = _parse_eval(text)
    assert result["score"] == 0.6


# ---------------------------------------------------------------------------
# _parse_validator_response
# ---------------------------------------------------------------------------

def test_parse_validator_response_raw_valid() -> None:
    text = '{"malicious": false, "confidence": 0.1, "categories": [], "reason": "Safe"}'
    result = _parse_validator_response(text)
    assert result["malicious"] is False
    assert result["confidence"] == 0.1
    assert result["categories"] == []
    assert result["reason"] == "Safe"


def test_parse_validator_response_fenced() -> None:
    text = '```json\n{"malicious": true, "confidence": 0.95, "categories": ["injection"], "reason": "Prompt injection detected"}\n```'
    result = _parse_validator_response(text)
    assert result["malicious"] is True
    assert result["confidence"] == 0.95
    assert result["categories"] == ["injection"]


def test_parse_validator_response_categories_not_list_normalised() -> None:
    text = '{"malicious": false, "confidence": 0.0, "categories": "none", "reason": ""}'
    result = _parse_validator_response(text)
    assert result["categories"] == []


def test_parse_validator_response_confidence_clamped() -> None:
    text = '{"malicious": true, "confidence": 2.0, "categories": [], "reason": "test"}'
    result = _parse_validator_response(text)
    assert result["confidence"] == 1.0


def test_parse_validator_response_invalid_json_returns_empty() -> None:
    result = _parse_validator_response("not json")
    assert result == {}


def test_parse_validator_response_missing_keys_returns_empty() -> None:
    result = _parse_validator_response('{"mode": "ok"}')
    assert result == {}


def test_parse_validator_response_reason_truncated_to_200() -> None:
    long_reason = "x" * 300
    text = f'{{"malicious": false, "confidence": 0.0, "categories": [], "reason": "{long_reason}"}}'
    result = _parse_validator_response(text)
    assert len(result["reason"]) == 200


def test_parse_validator_response_inline_in_prose() -> None:
    text = 'Analysis: {"malicious": false, "confidence": 0.05, "categories": [], "reason": "ok"} done.'
    result = _parse_validator_response(text)
    assert result["malicious"] is False


# ---------------------------------------------------------------------------
# _is_temperature_unsupported_error
# ---------------------------------------------------------------------------

def test_is_temperature_unsupported_error_must_be_1() -> None:
    assert _is_temperature_unsupported_error(Exception("temperature must be 1")) is True


def test_is_temperature_unsupported_error_only_supports() -> None:
    assert _is_temperature_unsupported_error(Exception("temperature only supports value 1")) is True


def test_is_temperature_unsupported_error_unsupported_keyword() -> None:
    assert _is_temperature_unsupported_error(Exception("temperature unsupported for this model")) is True


def test_is_temperature_unsupported_error_not_supported() -> None:
    assert _is_temperature_unsupported_error(Exception("temperature not supported")) is True


def test_is_temperature_unsupported_error_invalid_keyword() -> None:
    assert _is_temperature_unsupported_error(Exception("invalid temperature value")) is True


def test_is_temperature_unsupported_error_unrelated_exception() -> None:
    assert _is_temperature_unsupported_error(Exception("connection timed out")) is False


def test_is_temperature_unsupported_error_temperature_keyword_only() -> None:
    # "temperature" without a matching qualifier → False
    assert _is_temperature_unsupported_error(Exception("temperature 0.7 applied")) is False


# ---------------------------------------------------------------------------
# _chat_completion_with_empty_retry — retry path
# ---------------------------------------------------------------------------

def _make_completion_svc(responses: list[str]) -> SimpleNamespace:
    calls = iter(responses)

    def _chat(messages, *, deployment, temperature, timeout=45):
        return next(calls)

    return SimpleNamespace(
        _unwrap_answer=lambda t: t,
        _chat_completion=_chat,
        logger=Mock(),
    )


def test_chat_completion_with_empty_retry_returns_first_nonempty() -> None:
    svc = _make_completion_svc(["hello"])
    result = _chat_completion_with_empty_retry(
        [{"role": "user", "content": "q"}],
        deployment="dep",
        temperature=0.5,
        svc=svc,
    )
    assert result == "hello"


def test_chat_completion_with_empty_retry_retries_on_empty() -> None:
    svc = _make_completion_svc(["", "retried response"])
    result = _chat_completion_with_empty_retry(
        [{"role": "user", "content": "q"}],
        deployment="dep",
        temperature=0.5,
        svc=svc,
    )
    assert result == "retried response"
    svc.logger.warning.assert_called_once()


def test_chat_completion_with_empty_retry_non_1_temp_retries_at_1() -> None:
    """When initial temperature != 1.0 and response is empty, retry at 1.0."""
    recorded: list[float] = []

    def _chat(messages, *, deployment, temperature, timeout=45):
        recorded.append(temperature)
        return "" if temperature != 1.0 else "retry ok"

    svc = SimpleNamespace(
        _unwrap_answer=lambda t: t,
        _chat_completion=_chat,
        logger=Mock(),
    )
    result = _chat_completion_with_empty_retry(
        [{"role": "user", "content": "q"}],
        deployment="dep",
        temperature=0.3,
        svc=svc,
    )
    assert result == "retry ok"
    assert recorded[1] == 1.0


def test_chat_completion_with_empty_retry_at_1_retries_at_02() -> None:
    """When initial temperature == 1.0 and response is empty, retry at 0.2."""
    recorded: list[float] = []

    def _chat(messages, *, deployment, temperature, timeout=45):
        recorded.append(temperature)
        return "" if temperature == 1.0 else "alt ok"

    svc = SimpleNamespace(
        _unwrap_answer=lambda t: t,
        _chat_completion=_chat,
        logger=Mock(),
    )
    result = _chat_completion_with_empty_retry(
        [{"role": "user", "content": "q"}],
        deployment="dep",
        temperature=1.0,
        svc=svc,
    )
    assert result == "alt ok"
    assert recorded[1] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# _evaluate
# ---------------------------------------------------------------------------

def test_evaluate_calls_parse_eval_on_chat_response() -> None:
    svc = SimpleNamespace(
        EVALUATOR_PROMPT="eval prompt",
        config=SimpleNamespace(evaluator_deployment="eval-dep", evaluator_temperature=0.0),
        _chat_completion=Mock(
            return_value='{"acceptable": true, "score": 0.85, "reason": "Grounded."}'
        ),
        _parse_eval=_parse_eval,
    )
    result = _evaluate("What is MFA?", "MFA context", "MFA answer", svc=svc)
    assert result["acceptable"] is True
    assert result["score"] == 0.85
    svc._chat_completion.assert_called_once()


# ---------------------------------------------------------------------------
# _call_validator
# ---------------------------------------------------------------------------

def test_call_validator_disabled_returns_empty_dict() -> None:
    svc = SimpleNamespace(
        config=SimpleNamespace(prompt_injection_validator_enabled=False),
    )
    assert _call_validator("test text", svc=svc) == {}


def test_call_validator_enabled_returns_parsed_response() -> None:
    svc = SimpleNamespace(
        config=SimpleNamespace(
            prompt_injection_validator_enabled=True,
            prompt_injection_validator_deployment="validator-dep",
            prompt_injection_validator_temperature=0.0,
        ),
        VALIDATOR_SYSTEM_PROMPT="you are a validator",
        _chat_completion=Mock(
            return_value='{"malicious": false, "confidence": 0.05, "categories": [], "reason": "safe"}'
        ),
        _parse_validator_response=_parse_validator_response,
    )
    result = _call_validator("safe text", svc=svc)
    assert result["malicious"] is False


def test_call_validator_swallows_exception_returns_empty() -> None:
    svc = SimpleNamespace(
        config=SimpleNamespace(
            prompt_injection_validator_enabled=True,
            prompt_injection_validator_deployment="dep",
            prompt_injection_validator_temperature=0.0,
        ),
        VALIDATOR_SYSTEM_PROMPT="prompt",
        _chat_completion=Mock(side_effect=RuntimeError("network failure")),
        _parse_validator_response=_parse_validator_response,
    )
    result = _call_validator("test", svc=svc)
    assert result == {}


# late import so pytest is in scope for approx
import pytest
