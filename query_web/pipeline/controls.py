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
    "nist_ai_rmf": "NIST AI RMF",
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
    "ai",
    "rmf",
    "airmf",
    "risk",
    "management",
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

# Domain-specific expansion terms for policy-rule keywords.
# When a preferred framework is selected by rule, these terms augment the
# backfill query variants so framework controls using different phrasing are
# also retrieved.
_POLICY_KEYWORD_EXPANSION: dict[str, tuple[str, ...]] = {
    "encryption": (
        "encrypt",
        "encrypted",
        "cryptographic",
        "cryptography",
        "cipher",
        "tls",
        "aes",
        "rsa",
        "key management",
        "data at rest",
        "data in transit",
    ),
    "access": (
        "access control",
        "authentication",
        "authorisation",
        "authorization",
        "privileged access",
        "least privilege",
    ),
    "privileged": (
        "privileged access",
        "privileged user",
        "administrator",
        "admin",
        "elevated",
        "superuser",
    ),
    "backup": ("backup", "restore", "recovery", "retention", "resilience"),
}


# ---------------------------------------------------------------------------
# Evidence Corpus Helpers
# ---------------------------------------------------------------------------


def _normalise_evidence_corpus(raw_value: str) -> str | None:
    """Normalise an evidence corpus value to a canonical form.

    Args:
        raw_value: The raw evidence corpus value.

    Returns:
        The normalised evidence corpus value, or None if it cannot be normalised.
    """
    value = (raw_value or "").strip().lower()
    if not value:
        return None
    return _EVIDENCE_CORPUS_ALIASES.get(value)


def _normalise_evidence_corpora(values: Iterable[str] | None) -> list[str] | None:
    """Normalise a list of evidence corpus values to their canonical forms.

    Args:
        values: An iterable of raw evidence corpus values.

    Returns:
        A list of normalised evidence corpus values, or None if the input is None.
    """
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
    """Parse a CSV string of evidence corpora and normalise them.

    Args:
        raw_value: The raw CSV string of evidence corpora.

    Returns:
        A list of normalised evidence corpora, or None if the input is empty.
    """
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
    """Resolve the final list of evidence corpora to use, based on include/exclude lists.

    Args:
        include: An iterable of evidence corpora to include.
        exclude: An iterable of evidence corpora to exclude.
        default_corpora: An optional iterable of default evidence corpora to use if include is None.

    Returns:
        A list of resolved evidence corpora, in the order of default_corpora or _EVIDENCE_CORPUS_ORDER.
    """
    include_normalised = _normalise_evidence_corpora(include)
    exclude_normalised = set(_normalise_evidence_corpora(exclude) or [])

    if include is not None:
        base = include_normalised or []
    else:
        defaults = _normalise_evidence_corpora(default_corpora)
        base = defaults if defaults is not None else list(_EVIDENCE_CORPUS_ORDER)
    return [corpus for corpus in base if corpus not in exclude_normalised]


def _build_evidence_corpus_filter(selected_corpora: Iterable[str]) -> str | None:
    """Build a filter string for the selected evidence corpora.

    Args:
        selected_corpora: An iterable of selected evidence corpora.

    Returns:
        A filter string for the selected evidence corpora, or None if all corpora are selected.
    """
    selected_set = set(selected_corpora)
    selected = [c for c in _EVIDENCE_CORPUS_ORDER if c in selected_set]
    if not selected:
        return "__none__"
    if set(selected) == set(_EVIDENCE_CORPUS_ORDER):
        return None

    # Some historical ingestion paths populated corpus_role but not corpus.
    # Include role-based fallbacks so those documents remain retrievable.
    # Use simplified filter clauses without extra parentheses to avoid query_string parsing issues.
    clause_by_corpus = {
        "a": "corpus eq 'a'",
        "b": "corpus eq 'b' or corpus_role eq 'narrative_guidance'",
        "c": "corpus eq 'c' or corpus_role eq 'assessed_artifact'",
        # Legacy/untagged docs may have an empty corpus value.
        "legacy": "corpus eq 'legacy' or corpus eq ''",
    }

    clauses = [clause_by_corpus[corpus] for corpus in selected]
    if len(clauses) == 1:
        return clauses[0]
    # Wrap only the final joined expression, not each clause
    return " or ".join(f"({clause})" for clause in clauses)


# ---------------------------------------------------------------------------
# Framework Normalisation & Authority
# ---------------------------------------------------------------------------


def _normalise_framework_filter(raw_value: str | None, svc: Any) -> str | None:
    """Normalise a framework filter value to a canonical form.

    Args:
        raw_value: The raw framework filter value.
        svc: The service object providing access to the precedence policy.

    Returns:
        The canonical framework filter value, or None if the input is invalid.
    """
    if raw_value is None:
        return None

    value = raw_value.strip().lower()
    if not value or value in {"auto", "all", "any", "none"}:
        return None

    if value in _CONTROLS_FRAMEWORK_FILTERS:
        return _CONTROLS_FRAMEWORK_FILTERS[value]

    return svc._canonical_framework_name(value)


def _normalise_controls_comparison_mode(raw_value: str | None) -> str:
    """Normalise a controls comparison mode value to a canonical form.

    Args:
        raw_value: The raw controls comparison mode value.

    Returns:
        The canonical controls comparison mode value.
    """
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
    """Return the authority rank of a framework based on the precedence policy.

    Args:
        framework_name: The name of the framework.
        svc: The service object providing access to the precedence policy.

    Returns:
        The authority rank of the framework, or a default value if not found.
    """
    normalised = framework_name.strip().lower()
    for idx, configured in enumerate(svc.precedence_policy.default_framework_order):
        if normalised == configured.strip().lower():
            return idx
    return len(svc.precedence_policy.default_framework_order)


def _preferred_framework_for_question(question: str, svc: Any) -> str | None:
    """Determine the preferred framework for a given question based on the precedence policy.

    Args:
        question: The question text to evaluate.
        svc: The service object providing access to the precedence policy.

    Returns:
        The preferred framework for the question, or None if not found.
    """
    context = _preferred_framework_context_for_question(question, svc)
    if not context:
        return None
    preferred = context.get("preferred_framework")
    if isinstance(preferred, str) and preferred.strip():
        return preferred
    return None


def _preferred_framework_context_for_question(question: str, svc: Any) -> dict[str, Any] | None:
    """Determine the preferred framework context for a given question based on the precedence policy.

    Args:
        question: The question text to evaluate.
        svc: The service object providing access to the precedence policy.

    Returns:
        A dictionary containing the preferred framework context, or None if not found.
    """
    text = question.strip().lower()
    if not text:
        return None

    text_tokens = re.findall(r"[a-z0-9]+", text)

    def _stem(token: str) -> str:
        value = token.strip().lower()
        for suffix in ("ions", "ion", "ing", "ed", "es", "s"):
            if value.endswith(suffix) and len(value) > len(suffix) + 2:
                return value[: -len(suffix)]
        return value

    text_stems = {_stem(token) for token in text_tokens}

    def _keyword_matches_text(keyword: str) -> bool:
        key = keyword.strip().lower()
        if not key:
            return False
        if key in text:
            return True

        key_tokens = re.findall(r"[a-z0-9]+", key)
        if not key_tokens:
            return False
        if len(key_tokens) > 1:
            return all(_keyword_matches_text(part) for part in key_tokens)

        key_token = key_tokens[0]
        key_stem = _stem(key_token)

        if key_token in text_tokens or key_stem in text_stems:
            return True

        if len(key_stem) >= 5:
            for stem in text_stems:
                if stem.startswith(key_stem) or key_stem.startswith(stem):
                    return True

        return False

    for rule in svc.precedence_policy.rules:
        keywords = rule.get("applies_when_keywords")
        if not isinstance(keywords, list) or not keywords:
            continue

        normalised_keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
        if not normalised_keywords:
            continue

        if all(_keyword_matches_text(keyword) for keyword in normalised_keywords):
            preferred = svc._canonical_framework_name(str(rule.get("preferred_framework", "")))
            if preferred:
                return {
                    "preferred_framework": preferred,
                    "rule_id": str(rule.get("rule_id") or "").strip() or None,
                    "rule_description": str(rule.get("description") or "").strip() or None,
                    "matched_keywords": normalised_keywords,
                    "match_type": "policy_rule",
                }

    # Heuristic fallback when policy rules do not explicitly cover common intents.
    if any(term in text for term in ("backup", "backups", "recovery", "restore", "restoration")):
        return {
            "preferred_framework": "Essential Eight",
            "rule_id": None,
            "rule_description": "Heuristic fallback for backup/recovery intent.",
            "matched_keywords": ["backup", "recovery"],
            "match_type": "heuristic",
        }

    return None


def _precedence_policy_summary(svc: Any) -> str:
    """Generate a summary of the precedence policy.

    Args:
        svc: The service object providing access to the precedence policy.

    Returns:
        A string summarizing the precedence policy.
    """
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
    """Generate a coverage disclaimer for controls.

    Args:
        controls_debug: A dictionary containing debug information about controls.
        comparison_detected: A boolean indicating if a comparison was detected.
        comparison_mode: The mode of the controls comparison.

    Returns:
        A string containing the coverage disclaimer, or None if not applicable.
    """
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
    """Prepend a disclaimer to the answer text if not already present.

    Args:
        answer: The original answer text.
        disclaimer: The disclaimer text to prepend.

    Returns:
        The answer text with the disclaimer prepended if applicable.
    """
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
    """Extract focus terms from a question for query expansion.

    Args:
        question: The question text to process.

    Returns:
        A list of focus terms extracted from the question.
    """
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
    """Generate query variants for a given question.

    Args:
        question: The question text to process.

    Returns:
        A list of query variants.
    """
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
    """Merge two lists of control candidates, avoiding duplicates.

    Args:
        base_items: The base list of control candidates.
        new_items: The new list of control candidates to merge.

    Returns:
        A merged list of control candidates.
    """
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


def _fuse_controls_rankings(
    lexical_items: list[dict[str, Any]],
    vector_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fuse lexical and vector rankings with reciprocal-rank fusion.

    Args:
        lexical_items: A list of control candidates ranked by lexical search.
        vector_items: A list of control candidates ranked by vector search.

    Returns:
        A list of control candidates ranked by fused scores.
    """

    if not vector_items:
        return lexical_items
    if not lexical_items:
        return vector_items

    def _item_key(item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("requirement_id") or "").strip(),
            str(item.get("framework") or "").strip(),
            str(item.get("source_uri") or "").strip(),
        )

    fused_scores: dict[tuple[str, str, str], float] = {}
    best_items: dict[tuple[str, str, str], dict[str, Any]] = {}
    rrf_k = 60.0

    for rank, item in enumerate(lexical_items, start=1):
        key = _item_key(item)
        fused_scores[key] = fused_scores.get(key, 0.0) + (1.0 / (rrf_k + rank))
        best_items.setdefault(key, item)

    for rank, item in enumerate(vector_items, start=1):
        key = _item_key(item)
        fused_scores[key] = fused_scores.get(key, 0.0) + (1.0 / (rrf_k + rank))
        current_best = best_items.get(key)
        if current_best is None or float(item.get("score") or 0.0) > float(
            current_best.get("score") or 0.0
        ):
            best_items[key] = item

    ranked_keys = sorted(
        fused_scores.keys(),
        key=lambda key: (
            -fused_scores[key],
            -float(best_items[key].get("score") or 0.0),
        ),
    )
    return [best_items[key] for key in ranked_keys]


def _fetch_controls(
    search_text: str,
    retrieve_k: int,
    use_semantic: bool,
    framework_filter: str | None = None,
    svc: Any = None,
) -> list[dict[str, Any]]:
    """Execute a controls-index search and return hydrated items.

    Raises exceptions on error so callers can decide how to handle them.

    Args:
        search_text: The text to search for in the controls index.
        retrieve_k: The maximum number of items to retrieve.
        use_semantic: Whether to use semantic search.
        framework_filter: An optional framework filter to apply.
        svc: The service object providing access to the controls search client.

    Returns:
        A list of control candidates retrieved from the controls index.
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

    def _search_with_vector(vector_query: list[float] | None) -> list[dict[str, Any]]:
        """Perform a search with optional vector query and return hydrated items.

        Args:
            vector_query: An optional vector query for semantic search.

        Returns:
            A list of control candidates retrieved from the controls index.
        """
        items: list[dict[str, Any]] = []
        kwargs = dict(neutral_kwargs)
        if vector_query is not None:
            kwargs["vector_query"] = vector_query

        for r in svc.controls_search_client.search(
            query_text=search_text,
            top=retrieve_k,
            select=_SELECT,
            **kwargs,
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

    lexical_items = _search_with_vector(vector_query=None)

    vector: list[float] | None = None
    embed_query = getattr(svc, "_embed_query", None)
    if callable(embed_query):
        try:
            candidate = embed_query(search_text)
            if isinstance(candidate, list):
                vector = [float(v) for v in candidate]
        except Exception:
            vector = None

    vector_items = _search_with_vector(vector_query=vector) if vector else []
    return _fuse_controls_rankings(lexical_items, vector_items)


# ---------------------------------------------------------------------------
# Intent Detection & Diversity
# ---------------------------------------------------------------------------


def _is_cross_framework_comparison_intent(question: str) -> bool:
    """Determine if a question indicates cross-framework comparison intent.

    Args:
        question: The question text to analyse.

    Returns:
        True if the question indicates cross-framework comparison intent, False otherwise.
    """
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
    """Select a diverse set of controls from the given items.

    Args:
        items: A list of control items to select from.
        top_k: The maximum number of items to select.

    Returns:
        A list of selected control items.
    """
    if top_k <= 0 or not items:
        return []

    max_per_framework = max(1, (top_k + 1) // 2)
    max_per_family = max(1, (top_k + 1) // 2)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    framework_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}

    def _item_key(item: dict[str, Any]) -> str:
        """Generate a unique key for a control item based on its requirement ID, source URI, and requirement text.

        Args:
            item: A control item dictionary.

        Returns:
            A unique string key representing the control item.
        """
        requirement_id = str(item.get("requirement_id") or "").strip()
        source_uri = str(item.get("source_uri") or "").strip()
        requirement_text = str(item.get("requirement_text") or "").strip()
        return "||".join((requirement_id, source_uri, requirement_text[:120]))

    def _framework(item: dict[str, Any]) -> str:
        """Get the framework of a control item.

        Args:
            item: A control item dictionary.

        Returns:
            The framework of the control item as a lowercase string.
        """
        return str(item.get("framework") or "").strip().lower()

    def _family(item: dict[str, Any]) -> str:
        """Get the control family of a control item.

        Args:
            item: A control item dictionary.

        Returns:
            The control family of the control item as a lowercase string.
        """
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
    preferred_framework_debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Summarise the distribution of controls.

    Args:
        controls: A list of control items.
        controls_timings: A dictionary of control timings.
        preferred_framework: An optional preferred framework.
        preferred_framework_debug: An optional dictionary for preferred framework debug information.

    Returns:
        A dictionary summarising the distribution of controls.
    """
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
            "hybrid_enabled": bool(controls_timings.get("controls_hybrid_enabled", 0.0) >= 0.5),
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
            "precedence_rule_id": (
                preferred_framework_debug.get("rule_id")
                if isinstance(preferred_framework_debug, dict)
                else None
            ),
            "precedence_rule_description": (
                preferred_framework_debug.get("rule_description")
                if isinstance(preferred_framework_debug, dict)
                else None
            ),
            "precedence_rule_keywords": (
                preferred_framework_debug.get("matched_keywords")
                if isinstance(preferred_framework_debug, dict)
                else None
            ),
            "precedence_match_type": (
                preferred_framework_debug.get("match_type")
                if isinstance(preferred_framework_debug, dict)
                else None
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
    """Apply relevance-first ordering with authority preference as a tie-breaker.

    Args:
        items: A list of control items to rank.
        top_k: The maximum number of items to return.
        question: The question text to use for focus term extraction.
        svc: The service object providing access to the precedence policy.

    Returns:
        A list of ranked control items, limited to top_k.
    """
    preferred_framework = _preferred_framework_for_question(question, svc)
    focus_terms = _question_focus_terms(question)

    def _concept_overlap(item: dict[str, Any]) -> int:
        """Count the number of focus terms that appear in the control item.

        Args:
            item: A control item dictionary.

        Returns:
            The number of focus terms that appear in the control item.
        """
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
        """Determine the rank of a control item based on whether it matches the preferred framework.

        Args:
            item: A control item dictionary.

        Returns:
            The rank of the control item based on preferred framework match.
        """
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


def _control_concept_overlap_count(item: dict[str, Any], focus_terms: list[str]) -> int:
    """Count the number of focus terms that appear in the control item.

    Args:
        item: A control item dictionary.
        focus_terms: A list of focus terms to check for in the control item.

    Returns:
        The number of focus terms that appear in the control item.
    """
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


def _is_acceptable_preferred_backfill_candidate(
    candidate: dict[str, Any],
    question: str,
    current_slice: list[dict[str, Any]],
) -> bool:
    """Return True when a preferred-framework candidate is relevant enough.

    This prevents low-match filler controls from displacing stronger top-k items,
    while still allowing preferred-framework controls to backfill when cap crowding
    suppresses acceptable matches.

    Args:
        candidate: A control item dictionary to evaluate.
        question: The question text to use for focus term extraction.
        current_slice: The current slice of top-k control items.

    Returns:
        True if the candidate is relevant enough to be included, False otherwise.
    """

    focus_terms = _question_focus_terms(question)
    overlap = _control_concept_overlap_count(candidate, focus_terms)
    score = float(candidate.get("score") or 0.0)

    if not current_slice:
        return overlap > 0 or score >= 0.2

    current_scores = [float(item.get("score") or 0.0) for item in current_slice]
    current_min_score = min(current_scores) if current_scores else 0.0
    relative_floor = max(0.1, current_min_score * 0.6)

    # Strong concept alignment is sufficient unless the score is effectively noise.
    if overlap >= 1 and score >= 0.05:
        return True

    # Otherwise require score to be within a reasonable band of current top-k.
    if not focus_terms:
        return score >= relative_floor
    return score >= relative_floor and overlap > 0


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

    Args:
        question: The question text to search for.
        retrieve_k: The maximum number of items to retrieve.
        use_semantic: Whether to use semantic search.
        framework_filter_override: An optional framework filter to apply.
        comparison_mode: The mode of the controls comparison, either "auto-detect" or "force_cross_framework_comparison".
        svc: The service object providing access to the controls search client.

    Returns:
        A tuple containing a list of control candidates and a dictionary of timings.
    """
    timings: dict[str, float] = {}
    timings["controls_semantic_enabled"] = 1.0 if use_semantic else 0.0
    timings["controls_hybrid_enabled"] = (
        1.0 if callable(getattr(svc, "_embed_query", None)) else 0.0
    )
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
        """Fetch controls with a fallback from semantic to keyword search.

        Args:
            search_text: The text to search for in the controls index.
            top_k: The maximum number of items to retrieve.
            framework_name: An optional framework filter to apply.

        Returns:
            A list of control candidates.
        """
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

        # Build expanded query variants for the preferred-framework search.
        # When a policy rule fires its keywords may not match the framework's
        # own phrasing (e.g. "encrypted" vs ISM's "ASD-approved cryptographic
        # algorithm").  Augment with domain synonyms from the expansion table.
        backfill_variants = list(query_variants)
        preferred_fw_context = _preferred_framework_context_for_question(question, svc)
        if isinstance(preferred_fw_context, dict):
            matched_kws: list[str] = preferred_fw_context.get("matched_keywords") or []
            expansion_terms: list[str] = []
            for kw in matched_kws:
                expansion_terms.extend(_POLICY_KEYWORD_EXPANSION.get(kw, ()))
            if expansion_terms:
                backfill_variants.append(" ".join(expansion_terms[:8]))
                backfill_variants.append(" ".join(expansion_terms[:8]) + " control requirement")

        for variant in backfill_variants:
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

            # Hard-guarantee: if the preferred framework still has no
            # representation after re-ranking, replace the last item only when
            # a preferred-framework candidate has acceptable fitness.
            if items and not any(
                str(item.get("framework") or "") == preferred_framework for item in items
            ):
                best_preferred = next(
                    (
                        item
                        for item in re_ranked
                        if str(item.get("framework") or "") == preferred_framework
                    ),
                    None,
                )
                if best_preferred is None and backfill_items:
                    best_preferred = backfill_items[0]
                if best_preferred is not None and _is_acceptable_preferred_backfill_candidate(
                    best_preferred,
                    question,
                    items,
                ):
                    items[-1] = best_preferred

        timings["controls_preferred_framework_backfill_used"] = 1.0

    timings["controls_search_s"] = round(time.perf_counter() - t0, 3)
    return items, timings


# ---------------------------------------------------------------------------
# Corpus A framework ingestion status (moved from app.py)
# ---------------------------------------------------------------------------


def _controls_framework_ingestion_status(*, svc: Any) -> dict[str, Any]:
    """Return per-framework ingestion status by querying the controls search index.

    Args:
        svc: The service object providing access to the controls search client.

    Returns:
        A dictionary containing the ingestion status for each framework.
    """
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
