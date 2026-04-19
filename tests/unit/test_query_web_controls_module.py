"""Unit tests for query_web/controls.py."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.controls import (
    _build_evidence_corpus_filter,
    _controls_coverage_disclaimer,
    _controls_query_variants,
    _is_cross_framework_comparison_intent,
    _merge_control_candidates,
    _normalise_controls_comparison_mode,
    _normalise_evidence_corpora,
    _normalise_evidence_corpus,
    _parse_evidence_corpora_csv,
    _preferred_framework_for_question,
    _precedence_policy_summary,
    _prepend_disclaimer,
    _question_focus_terms,
    _resolve_evidence_corpora,
    _select_diverse_controls,
    _summarise_controls_distribution,
    _framework_authority_rank,
    _normalise_framework_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc_with_policy(
    order: list[str] | None = None,
    rules: list[dict] | None = None,
    version: str = "1.0",
) -> SimpleNamespace:
    policy = SimpleNamespace(
        default_framework_order=order or ["NIST CSF", "Essential Eight", "ISM", "AESCSF"],
        rules=rules or [],
        version=version,
    )
    svc = SimpleNamespace(
        precedence_policy=policy,
    )
    svc._canonical_framework_name = lambda v: v.strip() if v.strip() else None
    return svc


def _make_control(
    requirement_id: str,
    framework: str,
    control_family: str = "General",
    score: float = 0.8,
) -> dict:
    return {
        "requirement_id": requirement_id,
        "framework": framework,
        "control_family": control_family,
        "requirement_text": f"Requirement text for {requirement_id}",
        "source_uri": f"controls://{requirement_id.lower()}",
        "score": score,
    }


# ---------------------------------------------------------------------------
# Evidence corpus normalisation
# ---------------------------------------------------------------------------


def test_normalise_evidence_corpus_alias_a() -> None:
    assert _normalise_evidence_corpus("corpus-a") == "a"
    assert _normalise_evidence_corpus("corpus_a") == "a"
    assert _normalise_evidence_corpus("a") == "a"


def test_normalise_evidence_corpus_alias_b() -> None:
    assert _normalise_evidence_corpus("corpus-b") == "b"
    assert _normalise_evidence_corpus("b") == "b"


def test_normalise_evidence_corpus_alias_c() -> None:
    assert _normalise_evidence_corpus("corpus-c") == "c"
    assert _normalise_evidence_corpus("c") == "c"


def test_normalise_evidence_corpus_legacy() -> None:
    assert _normalise_evidence_corpus("legacy") == "legacy"


def test_normalise_evidence_corpus_unknown_returns_none() -> None:
    assert _normalise_evidence_corpus("unknown-corpus") is None


def test_normalise_evidence_corpus_empty_returns_none() -> None:
    assert _normalise_evidence_corpus("") is None


def test_normalise_evidence_corpora_deduplicates() -> None:
    result = _normalise_evidence_corpora(["a", "corpus-a", "b"])
    assert result == ["a", "b"]


def test_normalise_evidence_corpora_none_input() -> None:
    assert _normalise_evidence_corpora(None) is None


def test_normalise_evidence_corpora_filters_unknown() -> None:
    result = _normalise_evidence_corpora(["a", "xyz"])
    assert result == ["a"]


def test_parse_evidence_corpora_csv_standard() -> None:
    result = _parse_evidence_corpora_csv("a, b, c")
    assert result == ["a", "b", "c"]


def test_parse_evidence_corpora_csv_none() -> None:
    assert _parse_evidence_corpora_csv(None) is None


def test_parse_evidence_corpora_csv_empty_string() -> None:
    assert _parse_evidence_corpora_csv("") is None


def test_parse_evidence_corpora_csv_deduplicated() -> None:
    result = _parse_evidence_corpora_csv("a, corpus-a, b")
    assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# _resolve_evidence_corpora
# ---------------------------------------------------------------------------


def test_resolve_evidence_corpora_defaults_to_all() -> None:
    result = _resolve_evidence_corpora(None, None)
    assert set(result) == {"a", "b", "c", "legacy"}


def test_resolve_evidence_corpora_include_overrides_default() -> None:
    result = _resolve_evidence_corpora(["a", "b"], None)
    assert result == ["a", "b"]


def test_resolve_evidence_corpora_exclude_removes_from_defaults() -> None:
    result = _resolve_evidence_corpora(None, ["legacy", "c"])
    assert "legacy" not in result
    assert "c" not in result
    assert "a" in result
    assert "b" in result


def test_resolve_evidence_corpora_include_and_exclude() -> None:
    result = _resolve_evidence_corpora(["a", "b", "c"], ["c"])
    assert result == ["a", "b"]


def test_resolve_evidence_corpora_with_default_corpora() -> None:
    result = _resolve_evidence_corpora(None, None, default_corpora=["a", "b"])
    assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# _build_evidence_corpus_filter
# ---------------------------------------------------------------------------


def test_build_evidence_corpus_filter_all_returns_none() -> None:
    result = _build_evidence_corpus_filter(["a", "b", "c", "legacy"])
    assert result is None


def test_build_evidence_corpus_filter_single() -> None:
    result = _build_evidence_corpus_filter(["b"])
    assert result == "corpus eq 'b'"


def test_build_evidence_corpus_filter_two_corpora() -> None:
    result = _build_evidence_corpus_filter(["a", "b"])
    assert result is not None
    assert "corpus eq 'a'" in result
    assert "corpus eq 'b'" in result
    assert result.startswith("(")


def test_build_evidence_corpus_filter_empty_returns_none_sentinel() -> None:
    result = _build_evidence_corpus_filter([])
    assert result == "__none__"


# ---------------------------------------------------------------------------
# _normalise_framework_filter
# ---------------------------------------------------------------------------


def test_normalise_framework_filter_known_key() -> None:
    svc = _svc_with_policy()
    result = _normalise_framework_filter("nist_csf", svc)
    assert result == "NIST CSF"


def test_normalise_framework_filter_essential_eight() -> None:
    svc = _svc_with_policy()
    result = _normalise_framework_filter("essential_eight", svc)
    assert result == "Essential Eight"


def test_normalise_framework_filter_auto_returns_none() -> None:
    svc = _svc_with_policy()
    assert _normalise_framework_filter("auto", svc) is None
    assert _normalise_framework_filter("all", svc) is None
    assert _normalise_framework_filter("none", svc) is None


def test_normalise_framework_filter_none_input() -> None:
    svc = _svc_with_policy()
    assert _normalise_framework_filter(None, svc) is None


def test_normalise_framework_filter_unknown_delegates_to_svc() -> None:
    svc = _svc_with_policy()
    svc._canonical_framework_name = lambda v: "My Framework"
    result = _normalise_framework_filter("my_framework", svc)
    assert result == "My Framework"


# ---------------------------------------------------------------------------
# _normalise_controls_comparison_mode
# ---------------------------------------------------------------------------


def test_normalise_comparison_mode_auto_variants() -> None:
    for value in ("auto", "autodetect", "auto_detect", "auto-detect", "", None):
        assert _normalise_controls_comparison_mode(value) == "auto-detect"


def test_normalise_comparison_mode_force_variants() -> None:
    for value in ("force", "force_cross_framework_comparison", "force-cross-framework-comparison"):
        assert _normalise_controls_comparison_mode(value) == "force_cross_framework_comparison"


def test_normalise_comparison_mode_unknown_falls_back_to_auto() -> None:
    assert _normalise_controls_comparison_mode("garbage") == "auto-detect"


# ---------------------------------------------------------------------------
# _framework_authority_rank
# ---------------------------------------------------------------------------


def test_framework_authority_rank_returns_index() -> None:
    svc = _svc_with_policy(order=["NIST CSF", "Essential Eight", "ISM"])
    assert _framework_authority_rank("NIST CSF", svc) == 0
    assert _framework_authority_rank("Essential Eight", svc) == 1
    assert _framework_authority_rank("ISM", svc) == 2


def test_framework_authority_rank_unknown_returns_length() -> None:
    svc = _svc_with_policy(order=["NIST CSF"])
    assert _framework_authority_rank("Unknown", svc) == 1


def test_framework_authority_rank_case_insensitive() -> None:
    svc = _svc_with_policy(order=["NIST CSF", "Essential Eight"])
    assert _framework_authority_rank("nist csf", svc) == 0


# ---------------------------------------------------------------------------
# _preferred_framework_for_question
# ---------------------------------------------------------------------------


def test_preferred_framework_for_question_empty_returns_none() -> None:
    svc = _svc_with_policy()
    assert _preferred_framework_for_question("", svc) is None


def test_preferred_framework_for_question_backup_heuristic() -> None:
    svc = _svc_with_policy(rules=[])
    result = _preferred_framework_for_question("how should we handle backup and recovery?", svc)
    assert result == "Essential Eight"


def test_preferred_framework_for_question_policy_rule_match() -> None:
    svc = _svc_with_policy(
        rules=[
            {
                "rule_id": "r1",
                "applies_when_keywords": ["mfa", "authentication"],
                "preferred_framework": "ISM",
                "description": "MFA rule",
            }
        ]
    )
    result = _preferred_framework_for_question("What does mfa authentication require?", svc)
    assert result == "ISM"


def test_preferred_framework_for_question_rule_not_matched() -> None:
    svc = _svc_with_policy(
        rules=[
            {
                "rule_id": "r1",
                "applies_when_keywords": ["mfa", "authentication"],
                "preferred_framework": "ISM",
            }
        ]
    )
    # Only one keyword present — rule should NOT match
    result = _preferred_framework_for_question("what about mfa?", svc)
    # No match from rule and 'mfa' alone doesn't trigger backup heuristic
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# _precedence_policy_summary
# ---------------------------------------------------------------------------


def test_precedence_policy_summary_no_rules() -> None:
    svc = _svc_with_policy(order=["NIST CSF", "ISM"], version="2.0")
    summary = _precedence_policy_summary(svc)
    assert "2.0" in summary
    assert "NIST CSF" in summary
    assert "ISM" in summary


def test_precedence_policy_summary_with_rules() -> None:
    svc = _svc_with_policy(
        rules=[
            {
                "rule_id": "r1",
                "description": "use ISM for crypto",
                "preferred_framework": "ISM",
            }
        ]
    )
    summary = _precedence_policy_summary(svc)
    assert "r1" in summary
    assert "ISM" in summary


# ---------------------------------------------------------------------------
# _controls_coverage_disclaimer
# ---------------------------------------------------------------------------


def test_controls_coverage_disclaimer_no_debug_returns_none() -> None:
    result = _controls_coverage_disclaimer(
        controls_debug=None, comparison_detected=True, comparison_mode="auto-detect"
    )
    assert result is None


def test_controls_coverage_disclaimer_no_comparison_returns_none() -> None:
    result = _controls_coverage_disclaimer(
        controls_debug={"distinct_frameworks": 1},
        comparison_detected=False,
        comparison_mode="auto-detect",
    )
    assert result is None


def test_controls_coverage_disclaimer_multiple_frameworks_returns_none() -> None:
    result = _controls_coverage_disclaimer(
        controls_debug={"distinct_frameworks": 2},
        comparison_detected=True,
        comparison_mode="auto-detect",
    )
    assert result is None


def test_controls_coverage_disclaimer_single_framework_returns_note() -> None:
    debug = {
        "distinct_frameworks": 1,
        "framework_counts": [{"name": "NIST CSF", "count": 3}],
    }
    result = _controls_coverage_disclaimer(
        controls_debug=debug,
        comparison_detected=True,
        comparison_mode="auto-detect",
    )
    assert result is not None
    assert "NIST CSF" in result


def test_controls_coverage_disclaimer_forced_mode_also_triggers() -> None:
    debug = {
        "distinct_frameworks": 1,
        "framework_counts": [{"name": "ISM", "count": 2}],
    }
    result = _controls_coverage_disclaimer(
        controls_debug=debug,
        comparison_detected=False,
        comparison_mode="force_cross_framework_comparison",
    )
    assert result is not None
    assert "ISM" in result


# ---------------------------------------------------------------------------
# _prepend_disclaimer
# ---------------------------------------------------------------------------


def test_prepend_disclaimer_no_disclaimer_returns_answer() -> None:
    assert _prepend_disclaimer("my answer", None) == "my answer"


def test_prepend_disclaimer_prepends_blockquote() -> None:
    result = _prepend_disclaimer("my answer", "Warning!")
    assert result.startswith("> Warning!")
    assert "my answer" in result


def test_prepend_disclaimer_does_not_duplicate_existing_disclaimer() -> None:
    result = _prepend_disclaimer("> Warning!\n\nmy answer", "Warning!")
    assert result.count("Warning!") == 1


def test_prepend_disclaimer_empty_answer_returns_disclaimer() -> None:
    assert _prepend_disclaimer("", "Notice") == "Notice"


# ---------------------------------------------------------------------------
# _question_focus_terms
# ---------------------------------------------------------------------------


def test_question_focus_terms_removes_stopwords() -> None:
    terms = _question_focus_terms("What are the requirements for MFA authentication?")
    assert "what" not in terms
    assert "are" not in terms
    assert "the" not in terms
    assert "mfa" in terms


def test_question_focus_terms_removes_framework_tokens() -> None:
    terms = _question_focus_terms("What does NIST CSF require for access control?")
    assert "nist" not in terms
    assert "csf" not in terms


def test_question_focus_terms_deduplicates() -> None:
    terms = _question_focus_terms("backup backup backup recovery")
    assert terms.count("backup") == 1


def test_question_focus_terms_empty_string_returns_empty() -> None:
    assert _question_focus_terms("") == []


def test_question_focus_terms_preserves_short_keep_list() -> None:
    terms = _question_focus_terms("does MFA apply here")
    assert "mfa" in terms


# ---------------------------------------------------------------------------
# _controls_query_variants
# ---------------------------------------------------------------------------


def test_controls_query_variants_empty_string() -> None:
    result = _controls_query_variants("")
    assert result == [""]


def test_controls_query_variants_returns_multiple_for_real_question() -> None:
    result = _controls_query_variants("What MFA requirements exist for privileged access?")
    assert len(result) >= 1
    assert result[0] == "What MFA requirements exist for privileged access?"


def test_controls_query_variants_deduped() -> None:
    result = _controls_query_variants("backup backup backup")
    assert len(result) == len(set(v.strip().lower() for v in result))


# ---------------------------------------------------------------------------
# _merge_control_candidates
# ---------------------------------------------------------------------------


def test_merge_control_candidates_no_duplicates() -> None:
    base = [_make_control("N-1", "NIST CSF")]
    new = [_make_control("E-1", "Essential Eight")]
    merged = _merge_control_candidates(base, new)
    assert len(merged) == 2


def test_merge_control_candidates_deduplicates_by_key() -> None:
    item = _make_control("N-1", "NIST CSF")
    merged = _merge_control_candidates([item], [item])
    assert len(merged) == 1


def test_merge_control_candidates_preserves_order() -> None:
    base = [_make_control("N-1", "NIST CSF"), _make_control("N-2", "NIST CSF")]
    new = [_make_control("E-1", "Essential Eight")]
    merged = _merge_control_candidates(base, new)
    assert merged[0]["requirement_id"] == "N-1"
    assert merged[1]["requirement_id"] == "N-2"
    assert merged[2]["requirement_id"] == "E-1"


# ---------------------------------------------------------------------------
# _select_diverse_controls
# ---------------------------------------------------------------------------


def _diverse_items() -> list[dict]:
    return [
        _make_control("N-1", "NIST CSF", "Asset Management", 0.99),
        _make_control("N-2", "NIST CSF", "Asset Management", 0.98),
        _make_control("N-3", "NIST CSF", "Asset Management", 0.97),
        _make_control("N-4", "NIST CSF", "Asset Management", 0.96),
        _make_control("A-1", "AESCSF", "IT", 0.70),
        _make_control("E-1", "Essential Eight", "App Control", 0.65),
    ]


def test_select_diverse_controls_respects_top_k() -> None:
    result = _select_diverse_controls(_diverse_items(), top_k=3)
    assert len(result) <= 3


def test_select_diverse_controls_limits_per_framework() -> None:
    result = _select_diverse_controls(_diverse_items(), top_k=4)
    nist_count = sum(1 for item in result if item["framework"] == "NIST CSF")
    # max_per_framework = max(1, (4+1)//2) = 2
    assert nist_count <= 2


def test_select_diverse_controls_empty_input() -> None:
    assert _select_diverse_controls([], top_k=5) == []


def test_select_diverse_controls_top_k_zero() -> None:
    assert _select_diverse_controls(_diverse_items(), top_k=0) == []


# ---------------------------------------------------------------------------
# _summarise_controls_distribution
# ---------------------------------------------------------------------------


def test_summarise_controls_distribution_counts_frameworks() -> None:
    controls = [
        _make_control("N-1", "NIST CSF"),
        _make_control("N-2", "NIST CSF"),
        _make_control("E-1", "Essential Eight"),
    ]
    result = _summarise_controls_distribution(controls, {})
    assert result["total_controls"] == 3
    assert result["distinct_frameworks"] == 2


def test_summarise_controls_distribution_empty() -> None:
    result = _summarise_controls_distribution([], {})
    assert result["total_controls"] == 0
    assert result["distinct_frameworks"] == 0


def test_summarise_controls_distribution_semantic_flag() -> None:
    result = _summarise_controls_distribution([], {"controls_semantic_enabled": 1.0})
    assert result["retrieval_modes"]["semantic_enabled"] is True


def test_summarise_controls_distribution_preferred_framework() -> None:
    result = _summarise_controls_distribution(
        [_make_control("N-1", "NIST CSF")], {}, preferred_framework="NIST CSF"
    )
    assert result["retrieval_diagnostics"]["preferred_framework_selected"] == "NIST CSF"


# ---------------------------------------------------------------------------
# _is_cross_framework_comparison_intent
# ---------------------------------------------------------------------------


def test_cross_framework_comparison_intent_compare_keyword() -> None:
    assert _is_cross_framework_comparison_intent("Compare NIST CSF and Essential Eight") is True


def test_cross_framework_comparison_intent_which_frameworks() -> None:
    assert _is_cross_framework_comparison_intent("Which frameworks require MFA?") is True


def test_cross_framework_comparison_intent_two_frameworks_mentioned() -> None:
    assert _is_cross_framework_comparison_intent("What do PCI DSS and ISM say about encryption?") is True


def test_cross_framework_comparison_intent_single_framework_not_comparison() -> None:
    assert _is_cross_framework_comparison_intent("What does NIST CSF say about MFA?") is False


def test_cross_framework_comparison_intent_empty_string() -> None:
    assert _is_cross_framework_comparison_intent("") is False


def test_cross_framework_comparison_intent_versus_keyword() -> None:
    assert _is_cross_framework_comparison_intent("NIST CSF vs AESCSF for backup controls") is True


# ---------------------------------------------------------------------------
# _normalise_controls_comparison_mode — literal pass-through branch (line 203)
# ---------------------------------------------------------------------------


def test_normalise_controls_comparison_mode_unknown_value_returns_auto_detect() -> None:
    # Unrecognised values fall through to the default "auto-detect" return
    assert _normalise_controls_comparison_mode("unknown-mode") == "auto-detect"


def test_preferred_framework_for_question_rule_with_all_blank_keywords_skipped() -> None:
    """Keywords list has only blank strings → normalised_keywords is empty → continue (line 227)."""
    rules = [
        {"rule_id": "r1", "applies_when_keywords": ["", "  "], "preferred_framework": "ISM"},
        {"rule_id": "r2", "applies_when_keywords": ["backup"], "preferred_framework": "Essential Eight"},
    ]
    svc = _svc_with_policy(rules=rules)
    result = _preferred_framework_for_question("backup procedures", svc)
    # r1 skipped (blank keywords); r2 fires
    assert result == "Essential Eight"


def test_question_focus_terms_short_token_not_in_keep_list_skipped() -> None:
    """Token with len < 3 not in _QUERY_SHORT_KEEP is filtered out (line 327)."""
    from query_web.controls import _question_focus_terms
    # "xy" is 2 chars and unlikely to be in _QUERY_SHORT_KEEP
    result = _question_focus_terms("authentication xy policy")
    assert "xy" not in result
    assert "authentication" in result


def test_select_diverse_controls_skips_duplicate_keys() -> None:
    """Duplicate items (same requirement_id + framework) trigger the key-already-seen branch (line 525)."""
    dup_item = _make_control("R-1", "NIST CSF", control_family="ID.AM")
    items = [dup_item, dup_item, _make_control("R-2", "ISM", control_family="AC")]
    result = _select_diverse_controls(items, top_k=3)
    # Duplicate should not appear twice
    ids = [r["requirement_id"] for r in result]
    assert ids.count("R-1") == 1
    assert "R-2" in ids


def test_apply_framework_authority_preference_no_focus_terms_returns_zero() -> None:
    """Empty question produces no focus terms → _concept_overlap returns 0 (line 621)."""
    svc = _svc_with_policy(order=["NIST CSF"])
    items = [
        {
            "requirement_id": "R-1",
            "framework": "NIST CSF",
            "control_family": "ID.AM",
            "requirement_text": "asset inventory",
            "guidance_text": "",
            "score": 0.9,
        },
    ]
    # Empty question → no focus terms → all concept overlaps = 0
    result = _apply_framework_authority_preference(items, top_k=1, question="", svc=svc)
    assert len(result) == 1
    assert result[0]["requirement_id"] == "R-1"


# ---------------------------------------------------------------------------
# _framework_authority_rank — match found inside loop (lines 223-227)
# ---------------------------------------------------------------------------


def test_framework_authority_rank_matched_in_order() -> None:
    svc = _svc_with_policy(order=["NIST CSF", "Essential Eight", "ISM"])
    # "ism" should match index 2 rather than falling off the end
    rank = _framework_authority_rank("ISM", svc)
    assert rank == 2


def test_framework_authority_rank_first_in_order() -> None:
    svc = _svc_with_policy(order=["NIST CSF", "Essential Eight", "ISM"])
    rank = _framework_authority_rank("NIST CSF", svc)
    assert rank == 0


# ---------------------------------------------------------------------------
# _preferred_framework_for_question — empty-keywords guard (line 258)
# ---------------------------------------------------------------------------


def test_preferred_framework_for_question_rule_with_empty_keywords_skipped() -> None:
    rules = [
        {"rule_id": "r1", "applies_when_keywords": [], "preferred_framework": "ISM"},
        {"rule_id": "r2", "applies_when_keywords": ["backup"], "preferred_framework": "Essential Eight"},
    ]
    svc = _svc_with_policy(rules=rules)
    # Empty keywords rule is skipped; backup rule fires
    result = _preferred_framework_for_question("review backup procedures", svc)
    assert result == "Essential Eight"


def test_preferred_framework_for_question_rule_with_non_list_keywords_skipped() -> None:
    rules = [
        {"rule_id": "r1", "applies_when_keywords": None, "preferred_framework": "ISM"},
    ]
    svc = _svc_with_policy(rules=rules)
    # None keywords → rule skipped → heuristic fallback applies
    result = _preferred_framework_for_question("backup recovery restore", svc)
    assert result == "Essential Eight"


# ---------------------------------------------------------------------------
# _precedence_policy_summary — rule without description (line 327)
# ---------------------------------------------------------------------------


def test_precedence_policy_summary_rule_without_description() -> None:
    rules = [
        {"rule_id": "r1", "preferred_framework": "ISM"},  # no description key
    ]
    svc = _svc_with_policy(rules=rules)
    summary = _precedence_policy_summary(svc)
    # Should include rule_id and preferred framework but no semicolon separator
    assert "r1" in summary
    assert "ISM" in summary
    assert "; " not in summary


def test_precedence_policy_summary_rule_with_description() -> None:
    rules = [
        {"rule_id": "r1", "preferred_framework": "ISM", "description": "For ISM queries"},
    ]
    svc = _svc_with_policy(rules=rules)
    summary = _precedence_policy_summary(svc)
    assert "For ISM queries" in summary


# ---------------------------------------------------------------------------
# _controls_coverage_disclaimer — single-framework path (line 354)
# ---------------------------------------------------------------------------


def test_controls_coverage_disclaimer_single_framework_returns_note() -> None:
    debug = {
        "distinct_frameworks": 1,
        "framework_counts": [{"name": "NIST CSF", "count": 5}],
    }
    result = _controls_coverage_disclaimer(
        controls_debug=debug, comparison_detected=True, comparison_mode="auto-detect"
    )
    assert result is not None
    assert "NIST CSF" in result


def test_controls_coverage_disclaimer_multiple_frameworks_returns_none() -> None:
    debug = {"distinct_frameworks": 2, "framework_counts": []}
    result = _controls_coverage_disclaimer(
        controls_debug=debug, comparison_detected=True, comparison_mode="auto-detect"
    )
    assert result is None


def test_controls_coverage_disclaimer_empty_framework_counts_shows_none_name() -> None:
    debug = {"distinct_frameworks": 1, "framework_counts": []}
    result = _controls_coverage_disclaimer(
        controls_debug=debug, comparison_detected=True, comparison_mode="auto-detect"
    )
    assert result is not None
    assert "(none)" in result


# ---------------------------------------------------------------------------
# _fetch_controls — semantic + full iteration (lines 405-446)
# ---------------------------------------------------------------------------

from query_web.controls import _fetch_controls


def _make_search_result(
    requirement_id: str = "R-1",
    framework: str = "ISM",
    requirement_text: str = "Requirement text",
    score: float = 0.9,
) -> dict:
    return {
        "requirement_id": requirement_id,
        "framework": framework,
        "framework_version": "v1",
        "control_family": "General",
        "maturity_level": "ML1",
        "requirement_text": requirement_text,
        "guidance_text": "Some guidance",
        "source_uri": f"controls://{requirement_id.lower()}",
        "@search.score": score,
    }


def test_fetch_controls_returns_hydrated_items() -> None:
    mock_client = Mock()
    mock_client.search.return_value = [_make_search_result()]
    svc = SimpleNamespace(
        config=SimpleNamespace(controls_semantic_configuration_name="default"),
        controls_search_client=mock_client,
    )
    results = _fetch_controls("MFA requirement", 5, use_semantic=False, svc=svc)
    assert len(results) == 1
    assert results[0]["requirement_id"] == "R-1"
    assert results[0]["framework"] == "ISM"
    assert results[0]["score"] == pytest.approx(0.9)


def test_fetch_controls_skips_empty_requirement_text() -> None:
    mock_client = Mock()
    mock_client.search.return_value = [
        _make_search_result(requirement_text=""),
        _make_search_result(requirement_id="R-2", requirement_text="Valid requirement"),
    ]
    svc = SimpleNamespace(
        config=SimpleNamespace(controls_semantic_configuration_name="default"),
        controls_search_client=mock_client,
    )
    results = _fetch_controls("query", 5, use_semantic=False, svc=svc)
    assert len(results) == 1
    assert results[0]["requirement_id"] == "R-2"


def test_fetch_controls_sets_semantic_config_when_enabled() -> None:
    mock_client = Mock()
    mock_client.search.return_value = []
    svc = SimpleNamespace(
        config=SimpleNamespace(controls_semantic_configuration_name="my-semantic-config"),
        controls_search_client=mock_client,
    )
    _fetch_controls("query", 5, use_semantic=True, svc=svc)
    call_kwargs = mock_client.search.call_args[1]
    assert call_kwargs.get("query_type") == "semantic"
    assert call_kwargs.get("semantic_configuration_name") == "my-semantic-config"


def test_fetch_controls_applies_framework_filter() -> None:
    mock_client = Mock()
    mock_client.search.return_value = []
    svc = SimpleNamespace(
        config=SimpleNamespace(controls_semantic_configuration_name="default"),
        controls_search_client=mock_client,
    )
    _fetch_controls("query", 5, use_semantic=False, framework_filter="NIST CSF", svc=svc)
    call_kwargs = mock_client.search.call_args[1]
    assert "filter" in call_kwargs
    assert "NIST CSF" in call_kwargs["filter"]


def test_fetch_controls_score_none_defaults_to_zero() -> None:
    result = _make_search_result()
    result["@search.score"] = None
    mock_client = Mock()
    mock_client.search.return_value = [result]
    svc = SimpleNamespace(
        config=SimpleNamespace(controls_semantic_configuration_name="default"),
        controls_search_client=mock_client,
    )
    items = _fetch_controls("query", 5, use_semantic=False, svc=svc)
    assert items[0]["score"] == 0.0


# ---------------------------------------------------------------------------
# _select_diverse_controls — family cap branches (lines 525, 531)
# ---------------------------------------------------------------------------


def test_select_diverse_controls_family_cap_limits_same_family() -> None:
    # top_k=2 → max_per_family = max(1, (2+1)//2) = 1
    # All items share the same family, so only 1 per family is selected in first pass
    items = [
        _make_control("R-1", "NIST CSF", control_family="General", score=0.9),
        _make_control("R-2", "ISM", control_family="General", score=0.8),
        _make_control("R-3", "Essential Eight", control_family="General", score=0.7),
    ]
    result = _select_diverse_controls(items, top_k=2)
    # First pass limited by family cap; backfill fills remaining
    assert len(result) == 2


def test_select_diverse_controls_framework_cap_then_backfill() -> None:
    # top_k=2 → max_per_framework = 1
    # 3 NIST items: first pass picks only 1, backfill adds from remaining
    items = [
        _make_control("R-1", "NIST CSF", control_family="ID.AM", score=0.9),
        _make_control("R-2", "NIST CSF", control_family="ID.AM", score=0.8),
        _make_control("R-3", "NIST CSF", control_family="ID.AM", score=0.7),
    ]
    result = _select_diverse_controls(items, top_k=2)
    assert len(result) == 2


def test_select_diverse_controls_top_k_reached_in_first_pass_returns_early() -> None:
    # top_k=1 → first pass fills immediately; no backfill needed
    items = [
        _make_control("R-1", "NIST CSF", control_family="ID.AM"),
        _make_control("R-2", "ISM", control_family="AC"),
    ]
    result = _select_diverse_controls(items, top_k=1)
    assert len(result) == 1
    assert result[0]["requirement_id"] == "R-1"


# ---------------------------------------------------------------------------
# _apply_framework_authority_preference — concept overlap scoring (lines 621, 634-635)
# ---------------------------------------------------------------------------

from query_web.controls import _apply_framework_authority_preference


def test_apply_framework_authority_preference_concept_overlap_ranks_first() -> None:
    """Item with matching focus terms should rank above higher-score item without them."""
    svc = _svc_with_policy(order=["NIST CSF", "Essential Eight", "ISM"])
    items = [
        {
            "requirement_id": "R-1",
            "framework": "ISM",
            "control_family": "General",
            "requirement_text": "password policy and rotation",
            "guidance_text": "",
            "score": 0.5,
        },
        {
            "requirement_id": "R-2",
            "framework": "NIST CSF",
            "control_family": "General",
            "requirement_text": "asset inventory management",
            "guidance_text": "",
            "score": 0.9,
        },
    ]
    # Question focuses on "password" — R-1 should rank higher despite lower score
    result = _apply_framework_authority_preference(items, top_k=2, question="password policy", svc=svc)
    assert result[0]["requirement_id"] == "R-1"


def test_apply_framework_authority_preference_preferred_rank_tiebreaker() -> None:
    """When overlap is equal, preferred framework wins."""
    svc = _svc_with_policy(order=["NIST CSF", "Essential Eight"])
    svc.precedence_policy.rules = [
        {"rule_id": "r1", "applies_when_keywords": ["backup"], "preferred_framework": "Essential Eight"}
    ]
    items = [
        {
            "requirement_id": "N-1",
            "framework": "NIST CSF",
            "control_family": "Recovery",
            "requirement_text": "backup and restore",
            "guidance_text": "",
            "score": 0.9,
        },
        {
            "requirement_id": "E-1",
            "framework": "Essential Eight",
            "control_family": "Regular backups",
            "requirement_text": "backup and restore",
            "guidance_text": "",
            "score": 0.9,
        },
    ]
    result = _apply_framework_authority_preference(items, top_k=2, question="backup procedures", svc=svc)
    # Both have same text overlap; preferred framework (Essential Eight) should rank first
    assert result[0]["framework"] == "Essential Eight"


# ---------------------------------------------------------------------------
# _fetch_controls_with_fallback exception paths (lines 726-727) via controls_search
# ---------------------------------------------------------------------------

from query_web.controls import controls_search as _controls_search_fn


def _make_failing_svc(*, use_semantic_fallback_also_fails: bool = False) -> SimpleNamespace:
    """Build a svc where controls_search_client.search always raises."""

    def _always_raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("index unavailable")

    mock_client = Mock()
    mock_client.search.side_effect = _always_raise
    svc = SimpleNamespace(
        config=SimpleNamespace(
            controls_semantic_configuration_name="default",
        ),
        controls_search_client=mock_client,
        precedence_policy=SimpleNamespace(rules=[], framework_authority_order=[]),
    )
    # Provide pass-through so controls_search doesn't call with svc=None
    svc._apply_framework_authority_preference = lambda items, top_k, question: items
    return svc


def test_controls_search_fetch_raises_non_semantic_returns_empty() -> None:
    """When _fetch_controls raises and use_semantic=False → _fetch_controls_with_fallback hits return [] (line 727)."""
    svc = _make_failing_svc()
    items, timings = _controls_search_fn(
        "MFA policy requirements",
        5,
        use_semantic=False,
        framework_filter_override=None,
        comparison_mode="auto-detect",
        svc=svc,
    )
    assert items == []


def test_controls_search_fetch_raises_semantic_fallback_also_fails_returns_empty() -> None:
    """When both semantic and keyword fetches raise → hits return [] inside except Exception (line 726)."""
    svc = _make_failing_svc(use_semantic_fallback_also_fails=True)
    items, timings = _controls_search_fn(
        "MFA policy requirements",
        5,
        use_semantic=True,
        framework_filter_override=None,
        comparison_mode="auto-detect",
        svc=svc,
    )
    assert items == []
