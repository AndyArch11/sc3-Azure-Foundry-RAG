from __future__ import annotations

import os
from unittest.mock import Mock, patch

import requests

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
    assert app_module._is_cross_framework_comparison_intent(
        "Which framework requires inventory controls?"
    )
    assert app_module._is_cross_framework_comparison_intent(
        "Which frameworks requires an inventory?"
    )
    assert app_module._is_cross_framework_comparison_intent(
        "Compare NIST CSF vs Essential Eight for asset inventory"
    )
    assert not app_module._is_cross_framework_comparison_intent(
        "What does NIST CSF require for asset inventory?"
    )


def test_clean_markdown_whitespace_collapses_excess_blank_lines() -> None:
    raw = "Decision\n\n\n\nBody line\n\n\nNext section"
    cleaned = app_module._clean_markdown_whitespace(raw)
    assert cleaned == "Decision\n\nBody line\n\nNext section"


def test_clean_markdown_whitespace_trims_line_trailing_space() -> None:
    raw = "Line one   \nLine two\t\n\n\n"
    cleaned = app_module._clean_markdown_whitespace(raw)
    assert cleaned == "Line one\nLine two"


def test_question_focus_terms_extracts_generic_keywords() -> None:
    terms = app_module._question_focus_terms("Which frameworks require MFA for privileged access?")
    assert "mfa" in terms
    assert "privileged" in terms
    assert "frameworks" not in terms


def test_normalise_controls_comparison_mode_values() -> None:
    assert app_module._normalise_controls_comparison_mode(None) == "auto-detect"
    assert app_module._normalise_controls_comparison_mode("auto") == "auto-detect"
    assert (
        app_module._normalise_controls_comparison_mode("force_cross_framework_comparison")
        == "force_cross_framework_comparison"
    )
    assert app_module._normalise_controls_comparison_mode("unknown") == "auto-detect"


def test_select_diverse_controls_limits_single_framework_crowding() -> None:
    selected = app_module._select_diverse_controls(_controls_items_for_diversity(), top_k=5)
    frameworks = [str(item.get("framework")) for item in selected]

    assert len(selected) == 5
    assert len(set(frameworks)) >= 2
    assert frameworks.count("NIST CSF") <= 3


def test_apply_framework_authority_preference_prioritizes_concept_overlap() -> None:
    items = [
        {
            "requirement_id": "E-1",
            "framework": "Essential Eight",
            "control_family": "User application hardening",
            "requirement_text": "Disable legacy framework components.",
            "guidance_text": "",
            "score": 9.0,
        },
        {
            "requirement_id": "N-1",
            "framework": "NIST CSF",
            "control_family": "Identity and access management",
            "requirement_text": "MFA is required for privileged access.",
            "guidance_text": "",
            "score": 6.0,
        },
    ]

    ranked = app_module._apply_framework_authority_preference(
        items,
        top_k=2,
        question="Which frameworks require MFA?",
    )
    assert ranked[0]["requirement_id"] == "N-1"


def test_controls_search_comparison_enables_diversity_and_expands_fetch_k() -> None:
    with (
        patch.object(
            app_module,
            "_fetch_controls",
            return_value=_controls_items_for_diversity(),
        ) as fetch_mock,
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "Which framework has stronger inventory requirements?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override=None,
        )

    assert len(controls) == 5
    assert timings["controls_diversity_mode_enabled"] == 1.0
    assert any(
        call.args[1] == 20 and call.kwargs.get("framework_filter") is None
        for call in fetch_mock.call_args_list
    )


def test_controls_search_cross_framework_question_ignores_inferred_single_framework_filter() -> None:
    calls: list[tuple[str, int, bool, str | None]] = []

    def _fake_fetch(
        search_text: str,
        retrieve_k: int,
        use_semantic: bool,
        framework_filter: str | None = None,
    ) -> list[dict[str, object]]:
        calls.append((search_text, retrieve_k, use_semantic, framework_filter))
        return _controls_items_for_diversity()

    with (
        patch.object(app_module, "_fetch_controls", side_effect=_fake_fetch),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "How does NIST differ from Essential Eight?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override=None,
            comparison_mode="auto-detect",
        )

    assert len(controls) == 5
    assert timings["controls_comparison_detected"] == 1.0
    assert timings["controls_framework_filter_enabled"] == 0.0
    assert timings["controls_diversity_mode_enabled"] == 1.0
    assert any(call[3] is None for call in calls)


def test_controls_search_framework_override_disables_diversity() -> None:
    with (
        patch.object(
            app_module,
            "_fetch_controls",
            return_value=_controls_items_for_diversity(),
        ) as fetch_mock,
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
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


def test_controls_search_force_mode_enables_diversity_for_non_plural_question() -> None:
    with (
        patch.object(
            app_module,
            "_fetch_controls",
            return_value=_controls_items_for_diversity(),
        ),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "What does NIST CSF require for inventory?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override=None,
            comparison_mode="force_cross_framework_comparison",
        )

    assert len(controls) == 5
    assert timings["controls_comparison_detected"] == 0.0
    assert timings["controls_comparison_forced"] == 1.0
    assert timings["controls_diversity_mode_enabled"] == 1.0


def test_controls_search_diversity_backfills_framework_candidates() -> None:
    crowded_items = _controls_items_for_diversity()[:4]

    def _fake_fetch(
        search_text: str,
        retrieve_k: int,
        use_semantic: bool,
        framework_filter: str | None = None,
    ) -> list[dict[str, object]]:
        if framework_filter == "AESCSF":
            return [_controls_items_for_diversity()[4]]
        if framework_filter == "Essential Eight":
            return [_controls_items_for_diversity()[5]]
        if framework_filter is None:
            return crowded_items
        return []

    with (
        patch.object(
            app_module,
            "_fetch_controls",
            side_effect=_fake_fetch,
        ),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "Which framework requires an inventory?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override=None,
        )

    frameworks = {str(item.get("framework")) for item in controls}
    assert timings["controls_diversity_mode_enabled"] == 1.0
    assert "AESCSF" in frameworks
    assert "Essential Eight" in frameworks


def test_controls_search_plural_framework_query_enables_diversity() -> None:
    crowded_items = _controls_items_for_diversity()[:4]

    def _fake_fetch(
        search_text: str,
        retrieve_k: int,
        use_semantic: bool,
        framework_filter: str | None = None,
    ) -> list[dict[str, object]]:
        if framework_filter == "AESCSF":
            return [_controls_items_for_diversity()[4]]
        if framework_filter == "Essential Eight":
            return [_controls_items_for_diversity()[5]]
        if framework_filter is None:
            return crowded_items
        return []

    with (
        patch.object(
            app_module,
            "_fetch_controls",
            side_effect=_fake_fetch,
        ),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "Which frameworks requires an inventory?",
            retrieve_k=5,
            use_semantic=False,
            framework_filter_override=None,
        )

    frameworks = {str(item.get("framework")) for item in controls}
    assert timings["controls_diversity_mode_enabled"] == 1.0
    assert "AESCSF" in frameworks
    assert "Essential Eight" in frameworks


def test_embed_query_retries_on_429_then_succeeds() -> None:
    throttle_response = requests.Response()
    throttle_response.status_code = 429
    throttle_response.headers["Retry-After"] = "0"

    first_http = Mock()
    first_http.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests",
        response=throttle_response,
    )

    second_http = Mock()
    second_http.raise_for_status.return_value = None
    second_http.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    with (
        patch.object(app_module, "_cognitive_token", return_value="test-token"),
        patch.object(
            app_module.requests, "post", side_effect=[first_http, second_http]
        ) as post_mock,
        patch.object(app_module.time, "sleep") as sleep_mock,
    ):
        vector = app_module._embed_query("Which frameworks require MFA?")

    assert vector == [0.1, 0.2, 0.3]
    assert post_mock.call_count == 2
    sleep_mock.assert_called_once_with(0.75)


def test_controls_search_keyword_query_variants_recover_missed_matches() -> None:
    irrelevant = [
        {
            "requirement_id": "E-irrelevant-1",
            "framework": "Essential Eight",
            "control_family": "User application hardening",
            "requirement_text": "Disable legacy framework components.",
            "source_uri": "controls://e-irrelevant-1",
            "score": 9.0,
        },
        {
            "requirement_id": "I-irrelevant-1",
            "framework": "ISM",
            "control_family": "Guidelines for email",
            "requirement_text": "SPF is used to specify authorised email servers.",
            "source_uri": "controls://i-irrelevant-1",
            "score": 8.5,
        },
    ]
    keyword_hits = [
        {
            "requirement_id": "N-1",
            "framework": "NIST CSF",
            "control_family": "Identity and access management",
            "requirement_text": "Multi-factor authentication is required for privileged access.",
            "source_uri": "controls://n-1",
            "score": 7.0,
        },
        {
            "requirement_id": "A-1",
            "framework": "AESCSF",
            "control_family": "Access",
            "requirement_text": "MFA is enforced for administrative accounts.",
            "source_uri": "controls://a-1",
            "score": 6.9,
        },
    ]

    def _fake_fetch(
        search_text: str,
        retrieve_k: int,
        use_semantic: bool,
        framework_filter: str | None = None,
    ) -> list[dict[str, object]]:
        if framework_filter is None:
            if "mfa" in search_text.lower() or "multi-factor authentication" in search_text.lower():
                return keyword_hits
            return irrelevant
        return []

    with (
        patch.object(app_module, "_fetch_controls", side_effect=_fake_fetch),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "Which frameworks require MFA?",
            retrieve_k=4,
            use_semantic=False,
            framework_filter_override=None,
        )

    requirement_ids = {str(item.get("requirement_id")) for item in controls}
    assert "N-1" in requirement_ids
    assert "A-1" in requirement_ids
    assert timings["controls_query_variants"] >= 2.0


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


def test_controls_coverage_disclaimer_for_singular_framework_when_forced() -> None:
    controls = _controls_items_for_diversity()[:4]
    timings = {
        "controls_semantic_enabled": 1.0,
        "controls_framework_filter_enabled": 0.0,
        "controls_diversity_mode_enabled": 1.0,
    }
    summary = app_module._summarise_controls_distribution(controls, timings)

    note = app_module._controls_coverage_disclaimer(
        controls_debug=summary,
        comparison_detected=False,
        comparison_mode="force_cross_framework_comparison",
    )
    assert note is not None
    assert "only one framework" in note


def test_controls_coverage_disclaimer_not_added_for_multi_framework() -> None:
    controls = _controls_items_for_diversity()[:5]
    timings = {
        "controls_semantic_enabled": 1.0,
        "controls_framework_filter_enabled": 0.0,
        "controls_diversity_mode_enabled": 1.0,
    }
    summary = app_module._summarise_controls_distribution(controls, timings)

    note = app_module._controls_coverage_disclaimer(
        controls_debug=summary,
        comparison_detected=True,
        comparison_mode="auto-detect",
    )
    assert note is None


def test_retrieval_based_fallback_answer_flags_cross_framework_gap() -> None:
    controls = [
        {
            "requirement_id": "N-1",
            "framework": "NIST CSF",
            "source_uri": "controls://n-1",
        }
    ]
    chunks = [{"source_name": "cis-evidence.md", "source_uri": "blob://cis-evidence.md"}]

    answer = app_module._build_retrieval_based_fallback_answer(
        question="How does NIST differ from Essential Eight?",
        controls=controls,
        chunks=chunks,
    )

    assert "retrieval-grounded summary" in answer
    assert "cross-framework comparison" in answer
    assert "only one framework (NIST CSF)" in answer
    assert "## Corpus A Basis (Normative Requirements)" in answer


def test_retrieval_based_fallback_answer_preserves_corpus_b_vs_c_attribution() -> None:
    controls = [
        {
            "requirement_id": "E8-regular-backups-ML2-001",
            "framework": "Essential Eight",
        }
    ]
    corpus_b_chunks = [
        {
            "source_name": "c730dcc3ffbf59ac41d094aa92bb6bc42fb9e74c77b169075aaf844ce37751b7.pdf",
            "original_filename": "Weekly Backups Procedure.pdf",
            "source_uri": "blob://backups-guidance.pdf",
            "corpus": "b",
        }
    ]

    answer = app_module._build_retrieval_based_fallback_answer(
        question="What should be considered for backups?",
        controls=controls,
        chunks=corpus_b_chunks,
        corpus_b_chunks=corpus_b_chunks,
        corpus_c_chunks=[],
    )

    assert "## Corpus B Basis (Narrative Guidance)" in answer
    assert "Weekly Backups Procedure.pdf" in answer
    assert "c730dcc3ffbf59ac41d094aa92bb6bc42fb9e74c77b169075aaf844ce37751b7.pdf" not in answer
    assert "## Corpus C Basis (Assessed Artifacts/Evidence)" in answer
    assert "No Corpus C chunks were retrieved." in answer


def test_infer_framework_filter_aliases_and_unknown() -> None:
    assert app_module._infer_framework_filter("Need NIST CSF alignment") == "NIST CSF"
    assert app_module._infer_framework_filter("Essential Eight controls") == "Essential Eight"
    assert app_module._infer_framework_filter("AEMO guidance") == "AESCSF"
    assert app_module._infer_framework_filter("Information Security Manual requirement") == "ISM"
    assert app_module._infer_framework_filter("generic cyber security question") is None


def test_controls_search_semantic_unavailable_falls_back_to_keyword() -> None:
    calls: list[tuple[bool, str | None, int]] = []

    def _fake_fetch(
        search_text: str,
        retrieve_k: int,
        use_semantic: bool,
        framework_filter: str | None = None,
    ):
        calls.append((use_semantic, framework_filter, retrieve_k))
        if use_semantic:
            raise Exception("SemanticQueriesNotAvailable")
        return _controls_items_for_diversity()

    with (
        patch.object(app_module, "_fetch_controls", side_effect=_fake_fetch),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "What does NIST CSF require for inventory?",
            retrieve_k=3,
            use_semantic=True,
            framework_filter_override=None,
        )

    assert len(controls) == 3
    assert timings["controls_semantic_enabled"] == 1.0
    assert calls[0][0] is True
    assert calls[1][0] is False


def test_controls_search_diversity_backfill_semantic_error_falls_back_to_keyword() -> None:
    crowded_items = _controls_items_for_diversity()[:4]
    aes_item = _controls_items_for_diversity()[4]

    def _fake_fetch(
        search_text: str,
        retrieve_k: int,
        use_semantic: bool,
        framework_filter: str | None = None,
    ):
        if framework_filter is None:
            return crowded_items
        if framework_filter == "AESCSF" and use_semantic:
            raise Exception("backend semantic error")
        if framework_filter == "AESCSF" and not use_semantic:
            return [aes_item]
        return []

    with (
        patch.object(app_module, "_fetch_controls", side_effect=_fake_fetch),
        patch.object(
            app_module,
            "_apply_framework_authority_preference",
            side_effect=lambda items, top_k, question: items,
        ),
    ):
        controls, timings = app_module._controls_search(
            "Which framework requires an inventory?",
            retrieve_k=5,
            use_semantic=True,
            framework_filter_override=None,
        )

    frameworks = {str(item.get("framework")) for item in controls}
    assert timings["controls_diversity_mode_enabled"] == 1.0
    assert "AESCSF" in frameworks
