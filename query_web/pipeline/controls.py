"""Controls search, framework normalisation, and evidence corpus filtering."""

from __future__ import annotations

import re
import time
from typing import Any, Iterable

from runtime.assessment_orchestration._framework_patterns import (
    infer_single_framework as _infer_framework_filter,
)

# Module-level dependencies (resolved at call time from app.py):
# - config
# - controls_search_client
# - precedence_policy


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTROLS_FRAMEWORK_FILTERS = {
    "nist_csf": "NIST CSF",
    "essential_eight": "Essential Eight",
    "aescsf": "AESCSF",
    "cis_controls": "CIS Controls",
    "ism": "ISM",
    "pci_dss": "PCI DSS",
    "pspf": "PSPF",
}

_CONTROLS_COMPARISON_MODES = {
    "auto-detect",
    "force_cross_framework_comparison",
}

_EVIDENCE_CORPUS_ALIASES = {
    "a": "a",
    "corpus-a": "a",
    "corpus_a": "a",
    "b": "b",
    "corpus-b": "b",
    "corpus_b": "b",
    "c": "c",
    "corpus-c": "c",
    "corpus_c": "c",
    "legacy": "legacy",
}

_EVIDENCE_CORPUS_ORDER = ("a", "b", "c", "legacy")

_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "between",
    "by",
    "can",
    "does",
    "for",
    "framework",
    "frameworks",
    "from",
    "have",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "require",
    "required",
    "requires",
    "that",
    "the",
    "to",
    "what",
    "which",
}

_QUERY_FRAMEWORK_TOKENS = {
    "nists",
    "nist",
    "csf",
    "essential",
    "eight",
    "aescsf",
    "ism",
    "cis",
    "controls",
    "pci",
    "dss",
    "pspf",
}

_QUERY_SHORT_KEEP = {"mfa", "2fa", "iam", "sso"}


# ---------------------------------------------------------------------------
# Evidence Corpus Helpers
# ---------------------------------------------------------------------------


def _normalise_evidence_corpus(raw_value: str) -> str | None:
    value = (raw_value or "").strip().lower()
    if not value:
        return None
    return _EVIDENCE_CORPUS_ALIASES.get(value)


def _normalise_evidence_corpora(values: Iterable[str] | None) -> list[str] | None:
    if values is None:
        return None

    selected: list[str] = []
    seen: set[str] = set()
    for raw in values:
        normalised = _normalise_evidence_corpus(raw)
        if not normalised or normalised in seen:
            continue
        selected.append(normalised)
        seen.add(normalised)
    return selected


def _parse_evidence_corpora_csv(raw_value: str | None) -> list[str] | None:
    text = (raw_value or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return _normalise_evidence_corpora(parts)


def _resolve_evidence_corpora(
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
    *,
    default_corpora: Iterable[str] | None = None,
) -> list[str]:
    include_normalised = _normalise_evidence_corpora(include)
    exclude_normalised = set(_normalise_evidence_corpora(exclude) or [])

    if include is not None:
        base = include_normalised or []
    else:
        defaults = _normalise_evidence_corpora(default_corpora)
        base = defaults if defaults is not None else list(_EVIDENCE_CORPUS_ORDER)
    return [corpus for corpus in base if corpus not in exclude_normalised]


def _build_evidence_corpus_filter(selected_corpora: Iterable[str]) -> str | None:
    selected_set = set(selected_corpora)
    selected = [c for c in _EVIDENCE_CORPUS_ORDER if c in selected_set]
    if not selected:
        return "__none__"
    if set(selected) == set(_EVIDENCE_CORPUS_ORDER):
        return None
    if len(selected) == 1:
        return f"corpus eq '{selected[0]}'"
    clauses = [f"corpus eq '{corpus}'" for corpus in selected]
    return "(" + " or ".join(clauses) + ")"


# ---------------------------------------------------------------------------
# Framework Normalization & Authority
# ---------------------------------------------------------------------------


def _normalise_framework_filter(raw_value: str | None, svc: Any) -> str | None:
    if raw_value is None:
        return None

    value = raw_value.strip().lower()
    if not value or value in {"auto", "all", "any", "none"}:
        return None

    if value in _CONTROLS_FRAMEWORK_FILTERS:
        return _CONTROLS_FRAMEWORK_FILTERS[value]

    return svc._canonical_framework_name(value)


def _normalise_controls_comparison_mode(raw_value: str | None) -> str:
    value = (raw_value or "").strip().lower()
    if not value:
        return "auto-detect"
    if value in {"auto", "autodetect", "auto_detect", "auto-detect"}:
        return "auto-detect"
    if value in {
        "force",
        "force_cross_framework_comparison",
        "force-cross-framework-comparison",
        "force_cross_framework",
    }:
        return "force_cross_framework_comparison"
    return "auto-detect"


def _framework_authority_rank(framework_name: str, svc: Any) -> int:
    normalised = framework_name.strip().lower()
    for idx, configured in enumerate(svc.precedence_policy.default_framework_order):
        if normalised == configured.strip().lower():
            return idx
    return len(svc.precedence_policy.default_framework_order)


def _preferred_framework_for_question(question: str, svc: Any) -> str | None:
    text = question.strip().lower()
    if not text:
        return None

    for rule in svc.precedence_policy.rules:
        keywords = rule.get("applies_when_keywords")
        if not isinstance(keywords, list) or not keywords:
            continue

        normalised_keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
        if not normalised_keywords:
            continue

        if all(keyword in text for keyword in normalised_keywords):
            preferred = svc._canonical_framework_name(str(rule.get("preferred_framework", "")))
            if preferred:
                return preferred

    # Heuristic fallback when policy rules do not explicitly cover common intents.
    if any(term in text for term in ("backup", "backups", "recovery", "restore", "restoration")):
        return "Essential Eight"

    return None


def _precedence_policy_summary(svc: Any) -> str:
    order = " > ".join(svc.precedence_policy.default_framework_order)
    default_fw = getattr(svc.precedence_policy, "default_framework", "") or (
        svc.precedence_policy.default_framework_order[0]
        if svc.precedence_policy.default_framework_order
        else ""
    )
    governing_line = (
        f"Governing framework (default, unless a specific rule below applies): {default_fw}\n"
        if default_fw
        else ""
    )
    if not svc.precedence_policy.rules:
        return (
            f"Policy version: {svc.precedence_policy.version}\n"
            f"{governing_line}"
            f"Default framework precedence: {order}"
        )

    rule_lines = []
    for rule in svc.precedence_policy.rules[:5]:
        rule_id = str(rule.get("rule_id", "rule")).strip()
        description = str(rule.get("description", "")).strip()
        preferred = svc._canonical_framework_name(str(rule.get("preferred_framework", "")))
        preferred_text = preferred or str(rule.get("preferred_framework", "")).strip()
        if description:
            rule_lines.append(f"- {rule_id}: prefer {preferred_text}; {description}")
        else:
            rule_lines.append(f"- {rule_id}: prefer {preferred_text}")

    return (
        f"Policy version: {svc.precedence_policy.version}\n"
        f"{governing_line}"
        f"Default framework precedence: {order}\n"
        "Specific precedence rules (override the default only for the described topics):\n"
        + "\n".join(rule_lines)
    )


# ---------------------------------------------------------------------------
# Coverage Disclaimer
# ---------------------------------------------------------------------------


def _controls_coverage_disclaimer(
    *,
    controls_debug: dict[str, Any] | None,
    comparison_detected: bool,
    comparison_mode: str,
) -> str | None:
    if not controls_debug:
        return None

    forced = comparison_mode == "force_cross_framework_comparison"
    if not forced and not comparison_detected:
        return None

    distinct_frameworks = int(controls_debug.get("distinct_frameworks") or 0)
    if distinct_frameworks > 1:
        return None

    framework_counts = controls_debug.get("framework_counts")
    framework_name = "(none)"
    if isinstance(framework_counts, list) and framework_counts:
        first = framework_counts[0]
        if isinstance(first, dict):
            framework_name = str(first.get("name") or "(unknown)")

    return (
        "Coverage note: this query requests cross-framework comparison, "
        f"but retrieved controls came from only one framework ({framework_name}). "
        "Conclusions may be incomplete across frameworks without broader retrieval evidence."
    )


def _prepend_disclaimer(answer: str, disclaimer: str | None) -> str:
    text = (answer or "").strip()
    if not disclaimer:
        return text
    if disclaimer in text:
        return text
    if not text:
        return disclaimer
    return f"> {disclaimer}\n\n{text}"


# ---------------------------------------------------------------------------
# Query Processing
# ---------------------------------------------------------------------------


def _question_focus_terms(question: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", (question or "").lower())
    focus_terms: list[str] = []
    seen_terms: set[str] = set()
    for token in tokens:
        if token in _QUERY_STOPWORDS or token in _QUERY_FRAMEWORK_TOKENS:
            continue
        if len(token) < 3 and token not in _QUERY_SHORT_KEEP:
            continue
        if token in seen_terms:
            continue
        seen_terms.add(token)
        focus_terms.append(token)
    return focus_terms


def _controls_query_variants(question: str) -> list[str]:
    text = (question or "").strip()
    if not text:
        return [""]

    variants = [text]

    focus_terms = _question_focus_terms(text)

    if focus_terms:
        variants.append(" ".join(focus_terms))
        variants.append(" ".join([*focus_terms, "control", "requirement"]))

    # Preserve order while deduplicating.
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in variants:
        key = candidate.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    return deduped


# ---------------------------------------------------------------------------
# Control Candidate Processing
# ---------------------------------------------------------------------------


def _merge_control_candidates(
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(base_items)
    seen_keys = {
        (
            str(item.get("requirement_id") or "").strip(),
            str(item.get("framework") or "").strip(),
            str(item.get("source_uri") or "").strip(),
        )
        for item in base_items
    }

    for candidate in new_items:
        key = (
            str(candidate.get("requirement_id") or "").strip(),
            str(candidate.get("framework") or "").strip(),
            str(candidate.get("source_uri") or "").strip(),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(candidate)

    return merged


def _fetch_controls(
    search_text: str,
    retrieve_k: int,
    use_semantic: bool,
    framework_filter: str | None = None,
    svc: Any = None,
) -> list[dict[str, Any]]:
    """Execute a controls-index search and return hydrated items.

    Raises exceptions on error so callers can decide how to handle them.
    """
    _SELECT = [
        "requirement_id",
        "framework",
        "framework_version",
        "control_family",
        "maturity_level",
        "requirement_text",
        "guidance_text",
        "source_uri",
    ]
    neutral_kwargs: dict[str, Any] = {}
    if framework_filter:
        escaped_framework = framework_filter.replace("'", "''")
        neutral_kwargs["filters"] = f"framework eq '{escaped_framework}'"
    if use_semantic:
        neutral_kwargs["query_type"] = "semantic"
        neutral_kwargs["semantic_configuration_name"] = (
            svc.config.controls_semantic_configuration_name
        )

    items: list[dict[str, Any]] = []
    for r in svc.controls_search_client.search(
        query_text=search_text,
        top=retrieve_k,
        select=_SELECT,
        **neutral_kwargs,
    ):
        requirement_text = (r.get("requirement_text") or "").strip()
        if not requirement_text:
            continue
        score = r.get("@search.score")
        items.append(
            {
                "requirement_id": r.get("requirement_id") or "",
                "framework": r.get("framework") or "",
                "framework_version": r.get("framework_version") or "",
                "control_family": r.get("control_family") or "",
                "maturity_level": r.get("maturity_level"),
                "requirement_text": requirement_text,
                "guidance_text": (r.get("guidance_text") or "").strip(),
                "source_uri": r.get("source_uri") or "",
                "score": float(score) if score is not None else 0.0,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Intent Detection & Diversity
# ---------------------------------------------------------------------------


def _is_cross_framework_comparison_intent(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False

    comparison_patterns = (
        r"\bwhich\s+framework\b",
        r"\bwhich\s+frameworks\b",
        r"\bwhat\s+frameworks\b",
        r"\bframeworks(?:\s+(?:that|which))?\s+require\b",
        r"\bframeworks(?:\s+(?:that|which))?\s+requires\b",
        r"\bframeworks(?:\s+(?:that|which))?\s+contain\b",
        r"\bframeworks(?:\s+(?:that|which))?\s+contains\b",
        r"\bframeworks(?:\s+(?:that|which))?\s+has\b",
        r"\bframeworks(?:\s+(?:that|which))?\s+have\b",
        r"\bcompare\b",
        r"\bcomparison\b",
        r"\bvs\b",
        r"\bversus\b",
        r"\bacross\s+frameworks\b",
        r"\bbetween\b.*\band\b",
        r"\bstronger\b",
        r"\bmore\s+strict\b",
    )
    if any(re.search(pattern, text) for pattern in comparison_patterns):
        return True

    framework_patterns = {
        "NIST CSF": r"\bnist\b|\bnist\s*csf\b|\bcsf\s*2(\.0)?\b",
        "Essential Eight": r"\bessential\s*eight\b|\be8\b",
        "AESCSF": r"\baescsf\b",
        "ISM": r"\bism\b|\binformation\s+security\s+manual\b",
        "CIS Controls": r"\bcis\b|\bcis\s*controls\b",
        "PCI DSS": r"\bpci\b|\bpci\s*dss\b",
        "PSPF": r"\bpspf\b|\bprotective\s+security\s+policy\s+framework\b",
    }
    mentioned_frameworks = {
        framework for framework, pattern in framework_patterns.items() if re.search(pattern, text)
    }
    return len(mentioned_frameworks) >= 2


def _select_diverse_controls(items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    if top_k <= 0 or not items:
        return []

    max_per_framework = max(1, (top_k + 1) // 2)
    max_per_family = max(1, (top_k + 1) // 2)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    framework_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}

    def _item_key(item: dict[str, Any]) -> str:
        requirement_id = str(item.get("requirement_id") or "").strip()
        source_uri = str(item.get("source_uri") or "").strip()
        requirement_text = str(item.get("requirement_text") or "").strip()
        return "||".join((requirement_id, source_uri, requirement_text[:120]))

    def _framework(item: dict[str, Any]) -> str:
        return str(item.get("framework") or "").strip().lower()

    def _family(item: dict[str, Any]) -> str:
        return str(item.get("control_family") or "").strip().lower()

    for item in items:
        if len(selected) >= top_k:
            break
        key = _item_key(item)
        if key in selected_ids:
            continue
        framework = _framework(item)
        family = _family(item)
        if framework_counts.get(framework, 0) >= max_per_framework:
            continue
        if family and family_counts.get(family, 0) >= max_per_family:
            continue

        selected.append(item)
        selected_ids.add(key)
        framework_counts[framework] = framework_counts.get(framework, 0) + 1
        if family:
            family_counts[family] = family_counts.get(family, 0) + 1

    if len(selected) >= top_k:
        return selected[:top_k]

    for item in items:
        if len(selected) >= top_k:
            break
        key = _item_key(item)
        if key in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(key)

    return selected[:top_k]


# ---------------------------------------------------------------------------
# Distribution Summary
# ---------------------------------------------------------------------------


def _summarise_controls_distribution(
    controls: list[dict[str, Any]],
    controls_timings: dict[str, float],
    *,
    preferred_framework: str | None = None,
) -> dict[str, Any]:
    framework_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}

    for control in controls:
        framework = str(control.get("framework") or "").strip() or "(unknown)"
        family = str(control.get("control_family") or "").strip() or "(unknown)"
        framework_counts[framework] = framework_counts.get(framework, 0) + 1
        family_counts[family] = family_counts.get(family, 0) + 1

    def _as_sorted_items(counts: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"name": key, "count": value}
            for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
        ]

    return {
        "total_controls": len(controls),
        "distinct_frameworks": len(framework_counts),
        "distinct_control_families": len(family_counts),
        "framework_counts": _as_sorted_items(framework_counts),
        "control_family_counts": _as_sorted_items(family_counts),
        "retrieval_modes": {
            "semantic_enabled": bool(controls_timings.get("controls_semantic_enabled", 0.0) >= 0.5),
            "framework_filter_enabled": bool(
                controls_timings.get("controls_framework_filter_enabled", 0.0) >= 0.5
            ),
            "diversity_mode_enabled": bool(
                controls_timings.get("controls_diversity_mode_enabled", 0.0) >= 0.5
            ),
        },
        "retrieval_diagnostics": {
            "preferred_framework_selected": preferred_framework,
            "preferred_framework_backfill_used": bool(
                controls_timings.get("controls_preferred_framework_backfill_used", 0.0) >= 0.5
            ),
        },
    }


# ---------------------------------------------------------------------------
# Relevance Ranking
# ---------------------------------------------------------------------------


def _apply_framework_authority_preference(
    items: list[dict[str, Any]],
    top_k: int,
    question: str,
    svc: Any = None,
) -> list[dict[str, Any]]:
    """Apply relevance-first ordering with authority preference as a tie-breaker."""
    preferred_framework = _preferred_framework_for_question(question, svc)
    focus_terms = _question_focus_terms(question)

    def _concept_overlap(item: dict[str, Any]) -> int:
        if not focus_terms:
            return 0
        haystack = " ".join(
            [
                str(item.get("requirement_text") or "").lower(),
                str(item.get("control_family") or "").lower(),
                str(item.get("guidance_text") or "").lower(),
            ]
        )
        return sum(1 for term in focus_terms if term in haystack)

    def _preferred_rank(item: dict[str, Any]) -> int:
        if not preferred_framework:
            return 0
        framework = str(item.get("framework") or "").strip().lower()
        return 0 if framework == preferred_framework.lower() else 1

    ranked = sorted(
        items,
        key=lambda item: (
            -_concept_overlap(item),
            _preferred_rank(item),
            _framework_authority_rank(str(item.get("framework") or ""), svc),
            -float(item.get("score") or 0.0),
        ),
    )
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# Main Controls Search
# ---------------------------------------------------------------------------


def controls_search(
    question: str,
    retrieve_k: int,
    *,
    use_semantic: bool,
    framework_filter_override: str | None = None,
    comparison_mode: str = "auto-detect",
    svc: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Retrieve requirement records from the dedicated controls index.

    Resilient: falls back from semantic to keyword on FeatureNotSupported, and
    returns empty results (not an exception) for any other search failure so the
    query can still proceed with grounding-index context alone.
    """
    timings: dict[str, float] = {}
    timings["controls_semantic_enabled"] = 1.0 if use_semantic else 0.0
    detected_comparison = _is_cross_framework_comparison_intent(question)
    forced_comparison = comparison_mode == "force_cross_framework_comparison"

    explicit_framework_filter = framework_filter_override
    inferred_framework_filter = _infer_framework_filter(question)
    framework_filter = explicit_framework_filter or inferred_framework_filter
    if detected_comparison and explicit_framework_filter is None:
        framework_filter = None
    if forced_comparison and explicit_framework_filter is None:
        framework_filter = None

    timings["controls_framework_filter_enabled"] = 1.0 if framework_filter else 0.0
    timings["controls_authority_policy_enabled"] = 1.0
    diversity_mode = framework_filter is None and (detected_comparison or forced_comparison)
    preferred_framework = _preferred_framework_for_question(question, svc)
    timings["controls_preferred_framework"] = 1.0 if preferred_framework else 0.0
    timings["controls_preferred_framework_backfill_used"] = 0.0
    timings["controls_comparison_detected"] = 1.0 if detected_comparison else 0.0
    timings["controls_comparison_forced"] = 1.0 if forced_comparison else 0.0
    timings["controls_diversity_mode_enabled"] = 1.0 if diversity_mode else 0.0
    query_variants = _controls_query_variants(question)
    timings["controls_query_variants"] = float(len(query_variants))

    t0 = time.perf_counter()
    fetch_k = retrieve_k if framework_filter else max(retrieve_k, retrieve_k * 4)
    svc_fetch_controls = getattr(svc, "_fetch_controls", _fetch_controls)
    svc_apply_framework_authority_preference = getattr(
        svc,
        "_apply_framework_authority_preference",
        _apply_framework_authority_preference,
    )

    def _fetch_controls_with_fallback(
        search_text: str,
        *,
        top_k: int,
        framework_name: str | None,
    ) -> list[dict[str, Any]]:
        try:
            return svc_fetch_controls(
                search_text,
                top_k,
                use_semantic,
                framework_filter=framework_name,
            )
        except Exception:
            # Fall back to keyword search whenever semantic retrieval fails.
            if use_semantic:
                try:
                    return svc_fetch_controls(
                        search_text,
                        top_k,
                        use_semantic=False,
                        framework_filter=framework_name,
                    )
                except Exception:
                    return []
            return []

    items: list[dict[str, Any]] = []
    for variant in query_variants:
        variant_items = _fetch_controls_with_fallback(
            variant,
            top_k=fetch_k,
            framework_name=framework_filter,
        )
        items = _merge_control_candidates(items, variant_items)

    if diversity_mode:
        # Backfill candidates per framework so a single crowded top-k slice
        # cannot hide relevant controls from other frameworks.
        framework_backfill = (
            "Essential Eight",
            "ISM",
            "AESCSF",
            "NIST CSF",
            "CIS Controls",
            "PCI DSS",
            "PSPF",
        )
        per_framework_k = max(2, min(5, retrieve_k))

        for framework_name in framework_backfill:
            for variant in query_variants:
                framework_items = _fetch_controls_with_fallback(
                    variant,
                    top_k=per_framework_k,
                    framework_name=framework_name,
                )
                items = _merge_control_candidates(items, framework_items)

    ranked_items = svc_apply_framework_authority_preference(
        items,
        top_k=max(len(items), retrieve_k),
        question=question,
    )
    if diversity_mode:
        items = _select_diverse_controls(ranked_items, top_k=retrieve_k)
    else:
        items = ranked_items[:retrieve_k]

    # Preferred-framework backfill: checked AFTER final ranking/slice so that
    # low-scoring preferred-framework candidates that were retrieved but ranked
    # out of the top-k are still surfaced.
    if (
        not diversity_mode
        and framework_filter is None
        and preferred_framework
        and not any(str(item.get("framework") or "") == preferred_framework for item in items)
    ):
        per_framework_k = max(2, min(5, retrieve_k))
        backfill_items: list[dict[str, Any]] = []
        for variant in query_variants:
            framework_items = _fetch_controls_with_fallback(
                variant,
                top_k=per_framework_k,
                framework_name=preferred_framework,
            )
            backfill_items = _merge_control_candidates(backfill_items, framework_items)
        if backfill_items:
            combined = _merge_control_candidates(items, backfill_items)
            re_ranked = svc_apply_framework_authority_preference(
                combined,
                top_k=max(len(combined), retrieve_k),
                question=question,
            )
            items = re_ranked[:retrieve_k]
        timings["controls_preferred_framework_backfill_used"] = 1.0

    timings["controls_search_s"] = round(time.perf_counter() - t0, 3)
    return items, timings


# ---------------------------------------------------------------------------
# Corpus A framework ingestion status (moved from app.py)
# ---------------------------------------------------------------------------


def _controls_framework_ingestion_status(*, svc: Any) -> dict[str, Any]:
    """Return per-framework ingestion status by querying the controls search index."""
    status: dict[str, Any] = {}

    for key, framework_name in _CONTROLS_FRAMEWORK_FILTERS.items():
        escaped_framework = framework_name.replace("'", "''")
        filter_expr = f"framework eq '{escaped_framework}'"

        pager = svc.controls_search_client.search(
            search_text="*",
            filter=filter_expr,
            top=100,
            include_total_count=True,
            select=["framework_version", "ingestion_manifest_hash", "ingestion_loaded_at"],
        )
        versions: set[str] = set()
        manifests: set[str] = set()
        loaded_at_values: list[str] = []
        for item in pager:
            version = str(item.get("framework_version", "")).strip()
            if version:
                versions.add(version)
            manifest = str(item.get("ingestion_manifest_hash", "")).strip()
            if manifest:
                manifests.add(manifest)
            loaded_at = str(item.get("ingestion_loaded_at", "")).strip()
            if loaded_at:
                loaded_at_values.append(loaded_at)

        total = pager.get_count() or 0
        status[key] = {
            "framework": framework_name,
            "ingested": total > 0,
            "document_count": total,
            "framework_versions": sorted(versions),
            "manifest_hashes": sorted(manifests),
            "latest_loaded_at": max(loaded_at_values) if loaded_at_values else None,
        }

    return status
