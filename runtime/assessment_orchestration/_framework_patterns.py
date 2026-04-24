"""Canonical framework name recognition patterns.

Single source of truth for all framework-matching heuristics.  Used by
:mod:`polling_worker`, :mod:`assessment_runtime`, and :mod:`query_web.app`
so that keyword changes, new aliases, and priority decisions are made once.
"""

from __future__ import annotations

import json
import logging
import os
import re

# Load precedence policy from JSON


def _resolve_policy_path() -> str:
    """Run resolve policy path."""
    env_path = os.getenv("PRECEDENCE_POLICY_PATH", "").strip()
    if env_path:
        return env_path

    app_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    candidates = (
        os.path.join(app_root, "policies", "precedence_policy.json"),
        os.path.join(app_root, "query_web", "policies", "precedence_policy.json"),
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


_POLICY_PATH = _resolve_policy_path()
try:
    with open(_POLICY_PATH, "r", encoding="utf-8") as f:
        _POLICY = json.load(f)
except Exception as e:
    logging.warning(f"Could not load precedence policy file at {_POLICY_PATH}: {e}")
    _POLICY = {}

# Canonical multi-framework output order (used when "all frameworks" is requested).
ALL_FRAMEWORK_ORDER: tuple[str, ...] = (
    tuple(_POLICY["default_framework_order"])
    if "default_framework_order" in _POLICY
    else (
        "Essential Eight",
        "ISM",
        "AESCSF",
        "NIST AI RMF",
        "NIST CSF",
        "PSPF",
        "PCI DSS",
        "CIS Controls",
    )
)

# Default framework for scope/clarification
DEFAULT_FRAMEWORK: str = _POLICY.get("default_framework", "Essential Eight")

# ---------------------------------------------------------------------------
# Pattern table — ordered by canonical display / output preference.
# ---------------------------------------------------------------------------

FRAMEWORK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Essential Eight",
        re.compile(
            r"\b(essential\s*eight|essential_eight|essential\s*8|\be8\b)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "AESCSF",
        re.compile(
            r"\b(aescsf|aemo|australian\s+energy\s+sector\s+cyber\s+security\s+framework)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ISM",
        re.compile(r"\b(ism|information\s+security\s+manual)\b", re.IGNORECASE),
    ),
    (
        "NIST AI RMF",
        re.compile(
            r"\b(nist\s*ai\s*rmf|ai\s*risk\s*management\s*framework|nist\s*ai\s*100-1|airmf)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "NIST CSF",
        re.compile(r"\b(nist\s*csf|nist|csf\s*2(\.0)?)\b", re.IGNORECASE),
    ),
    (
        "PSPF",
        re.compile(r"\b(pspf|protective\s+security\s+policy\s+framework)\b", re.IGNORECASE),
    ),
    (
        "PCI DSS",
        re.compile(r"\b(pci\s*dss|pci[-_\s]?dss\s*v?4(\.0(\.1)?)?)\b", re.IGNORECASE),
    ),
    (
        "CIS Controls",
        re.compile(
            r"\b(cis\s*controls?|cis_controls|critical\s+security\s+controls?)\b",
            re.IGNORECASE,
        ),
    ),
)


# Phrases that unambiguously request all frameworks at once.
ALL_FRAMEWORK_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(all\s+frameworks)\b", re.IGNORECASE),
    re.compile(
        r"\b(review|assess|evaluate)\s+.*\b(all\s+(controls\s+)?frameworks)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(full|complete)\s+(framework\s+)?review\b", re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# Helper patterns used internally by both matching functions.
# ---------------------------------------------------------------------------

_GENERIC_CSF_PHRASE_RE = re.compile(r"\bcyber\s+security\s+framework\b", re.IGNORECASE)
_FULL_AES_PHRASE_RE = re.compile(
    r"\baustralian\s+energy\s+sector\s+cyber\s+security\s+framework\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Single-match priority order — most specific / longest tokens first so that
# short tokens like 'ism', 'nist', 'e8' don't overshadow longer matches.
# ---------------------------------------------------------------------------

_INFER_ORDER: tuple[str, ...] = (
    "PSPF",
    "PCI DSS",
    "CIS Controls",
    "AESCSF",
    "NIST CSF",
    "Essential Eight",
    "ISM",
)
_PATTERN_BY_FRAMEWORK: dict[str, re.Pattern[str]] = dict(FRAMEWORK_PATTERNS)
_INFER_PRIORITY: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, _PATTERN_BY_FRAMEWORK[name]) for name in _INFER_ORDER
)


# ---------------------------------------------------------------------------
# Public matching functions
# ---------------------------------------------------------------------------


def is_explicit_all_framework_request(text: str) -> bool:
    """Return ``True`` if *text* unambiguously requests all frameworks."""
    value = text.strip()
    if not value:
        return False
    return any(p.search(value) for p in ALL_FRAMEWORK_INTENT_PATTERNS)


def requested_frameworks_from_text(text: str) -> tuple[str, ...]:
    """Return every framework explicitly named or implied in *text*.

    Returns :data:`ALL_FRAMEWORK_ORDER` when an all-frameworks intent phrase
    is detected.  Returns ``()`` when no framework can be resolved.
    """
    import logging

    value = text.strip()
    logging.info(f"[framework_patterns] Matching frameworks in text: {repr(value)}")
    if not value:
        logging.info("[framework_patterns] No text provided for framework detection.")
        return ()
    if is_explicit_all_framework_request(value):
        logging.info("[framework_patterns] Detected all-frameworks intent.")
        return ALL_FRAMEWORK_ORDER

    found: list[str] = []
    for framework, pattern in FRAMEWORK_PATTERNS:
        if pattern.search(value) and framework not in found:
            found.append(framework)

    # "cyber security framework" (generic) implies NIST CSF, unless the full
    # Australian Energy Sector phrase was used (already captured as AESCSF).
    if _GENERIC_CSF_PHRASE_RE.search(value) and not _FULL_AES_PHRASE_RE.search(value):
        if "NIST CSF" not in found:
            found.append("NIST CSF")

    logging.info(f"[framework_patterns] Frameworks detected: {found}")
    return tuple(found)


def infer_single_framework(text: str) -> str | None:
    """Return the single highest-priority framework inferred from *text*.

    Uses a specificity-first priority order (:data:`_INFER_ORDER`) to minimise
    false-positive matches on short tokens.  Returns ``None`` when no framework
    is recognised.

    Use :func:`requested_frameworks_from_text` when multi-framework detection
    or all-frameworks expansion is needed.
    """
    value = text.strip()
    if not value:
        return None
    for framework, pattern in _INFER_PRIORITY:
        if pattern.search(value):
            return framework
    return None
