from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web import app as app_module


def _controls_items_for_diversity() -> list[dict[str, object]]:
    return [
        {
            "requirement_id": "N-1",
            "framework": "NIST CSF",
            "control_family": "Asset Management",
            "requirement_text": "Maintain inventory of hardware assets.",
            "source_uri": "controls://n-1",
            "score": 0.99,
        },
        {
            "requirement_id": "N-2",
            "framework": "NIST CSF",
            "control_family": "Asset Management",
            "requirement_text": "Maintain inventory of software assets.",
            "source_uri": "controls://n-2",
            "score": 0.98,
        },
        {
            "requirement_id": "N-3",
            "framework": "NIST CSF",
            "control_family": "Asset Management",
            "requirement_text": "Maintain inventory of services.",
            "source_uri": "controls://n-3",
            "score": 0.97,
        },
        {
            "requirement_id": "N-4",
            "framework": "NIST CSF",
            "control_family": "Asset Management",
            "requirement_text": "Maintain inventory of data assets.",
            "source_uri": "controls://n-4",
            "score": 0.96,
        },
        {
            "requirement_id": "A-1",
            "framework": "AESCSF",
            "control_family": "IT",
            "requirement_text": "Maintain inventory of IT assets.",
            "source_uri": "controls://a-1",
            "score": 0.70,
        },
        {
            "requirement_id": "E-1",
            "framework": "Essential Eight",
            "control_family": "Application Control",
            "requirement_text": "Maintain inventory of approved applications.",
            "source_uri": "controls://e-1",
            "score": 0.65,
        },
    ]


def test_cross_framework_comparison_intent_detection() -> None:
    assert app_module._is_cross_framework_comparison_intent("Which framework requires inventory controls?")
    assert app_module._is_cross_framework_comparison_intent("Compare NIST CSF vs Essential Eight for asset inventory")
    assert not app_module._is_cross_framework_comparison_intent("What does NIST CSF require for asset inventory?")


def test_select_diverse_controls_limits_single_framework_crowding() -> None:
    selected = app_module._select_diverse_controls(_controls_items_for_diversity(), top_k=5)
    frameworks = [str(item.get("framework")) for item in selected]

    assert len(selected) == 5
    assert len(set(frameworks)) >= 2
    assert frameworks.count("NIST CSF") <= 3


def test_controls_search_comparison_enables_diversity_and_expands_fetch_k() -> None:
    with patch.object(
        app_module,
        "_fetch_controls",
        return_value=_controls_items_for_diversity(),
    ) as fetch_mock, patch.object(
        app_module,
        "_apply_framework_authority_preference",
        side_effect=lambda items, top_k, question: items,
    ):
        controls, timings = app_module._controls_search(
            "Which framework has stronger inventory requirements?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override=None,
        )

    assert len(controls) == 5
    assert timings["controls_diversity_mode_enabled"] == 1.0
    assert fetch_mock.call_args.args[1] == 20


def test_controls_search_framework_override_disables_diversity() -> None:
    with patch.object(
        app_module,
        "_fetch_controls",
        return_value=_controls_items_for_diversity(),
    ) as fetch_mock, patch.object(
        app_module,
        "_apply_framework_authority_preference",
        side_effect=lambda items, top_k, question: items,
    ):
        controls, timings = app_module._controls_search(
            "Which framework has stronger inventory requirements?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override="NIST CSF",
        )

    assert len(controls) == 5
    assert timings["controls_diversity_mode_enabled"] == 0.0
    assert fetch_mock.call_args.args[1] == 5


def test_summarise_controls_distribution_includes_framework_and_family_counts() -> None:
    controls = _controls_items_for_diversity()[:4] + _controls_items_for_diversity()[4:5]
    timings = {
        "controls_semantic_enabled": 1.0,
        "controls_framework_filter_enabled": 0.0,
        "controls_diversity_mode_enabled": 1.0,
    }

    summary = app_module._summarise_controls_distribution(controls, timings)

    assert summary["total_controls"] == 5
    assert summary["distinct_frameworks"] == 2
    assert summary["distinct_control_families"] == 2
    assert summary["framework_counts"][0]["name"] == "NIST CSF"
    assert summary["framework_counts"][0]["count"] == 4
    assert summary["retrieval_modes"]["semantic_enabled"] is True
    assert summary["retrieval_modes"]["diversity_mode_enabled"] is True