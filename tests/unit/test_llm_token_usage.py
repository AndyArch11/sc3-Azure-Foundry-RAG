"""Unit tests for runtime.llm.token_usage."""

from __future__ import annotations

from runtime.llm.token_usage import (
    estimate_tokens_from_text,
    pop_last_token_usage,
    record_token_usage,
)


def test_record_and_pop_round_trip() -> None:
    record_token_usage(prompt_tokens=10, completion_tokens=5)
    usage = pop_last_token_usage()
    assert usage is not None
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 15
    assert usage.estimated is False


def test_record_with_explicit_total() -> None:
    record_token_usage(prompt_tokens=3, completion_tokens=4, total_tokens=100, estimated=True)
    usage = pop_last_token_usage()
    assert usage is not None
    assert usage.total_tokens == 100
    assert usage.estimated is True


def test_pop_clears_state() -> None:
    record_token_usage(prompt_tokens=1, completion_tokens=1)
    assert pop_last_token_usage() is not None
    assert pop_last_token_usage() is None


def test_estimate_tokens_from_text() -> None:
    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("a") == 1
    assert estimate_tokens_from_text("a" * 400) == 100
