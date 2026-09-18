"""Fast, deterministic UI tests for the Query Console tab navigation.

These do not call the LLM/search pipeline — they only verify that the static
page renders and that client-side tab switching (query_web/static/index.js)
shows/hides the right panels. Safe to run against any local stack, even one
without Ollama models pulled yet.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


def test_homepage_loads_with_ask_tab_active(page: Page, base_url: str) -> None:
    """The Ask tab is active by default and its form controls are visible."""
    page.goto(base_url)

    expect(page.locator("#tab-btn-ask")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#tab-ask")).to_be_visible()
    expect(page.locator("#question")).to_be_visible()
    expect(page.locator("#ask-submit-btn")).to_be_visible()


def test_switching_to_assess_tab_shows_compliance_report_panel(page: Page, base_url: str) -> None:
    """Clicking the Assess tab reveals the compliance report question field."""
    page.goto(base_url)

    page.locator("#tab-btn-assess").click()

    expect(page.locator("#tab-btn-assess")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#tab-assess")).to_be_visible()
    expect(page.locator("#tab-ask")).to_be_hidden()
    expect(page.locator("#cr-question")).to_be_visible()


def test_switching_to_reference_and_graph_tabs(page: Page, base_url: str) -> None:
    """Reference and Graph tabs render their panels and hide the others."""
    page.goto(base_url)

    page.locator("#tab-btn-reference").click()
    expect(page.locator("#tab-reference")).to_be_visible()
    expect(page.locator("#tab-ask")).to_be_hidden()

    page.locator("#tab-btn-graph").click()
    expect(page.locator("#tab-graph")).to_be_visible()
    expect(page.locator("#tab-reference")).to_be_hidden()
