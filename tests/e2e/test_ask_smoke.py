"""End-to-end Ask flow smoke test.

Unlike test_navigation.py, this exercises the full retrieval + LLM pipeline
(Ollama/Azure OpenAI/Bedrock depending on CLOUD_PROVIDER), so it is slower and
requires the target stack to actually be able to answer questions (models
pulled, at least one corpus populated). Marked so it can be excluded from a
fast local run with `-m "not e2e_llm"`.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_llm


def test_submitting_a_question_renders_an_answer_or_explicit_error(
    page: Page, base_url: str, auth_token: str
) -> None:
    """Submitting the Ask form produces either a rendered answer or a visible error.

    This deliberately does not assert on answer *content* (LLM output is
    non-deterministic) — it verifies the end-to-end request/response wiring
    between the form, /ask, and the rendered result panel.
    """
    page.goto(base_url)

    if auth_token:
        page.locator("#auth_token").fill(auth_token)

    page.locator("#question").fill("What is this system used for?")
    ask_button = page.locator("#ask-submit-btn")
    expect(ask_button).to_be_enabled(timeout=120_000)
    ask_button.click(timeout=120_000)

    result_panel = page.locator("#answer-md, .answer, #ask-results-section")
    expect(result_panel.first).to_be_visible(timeout=120_000)
