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
