"""Base classes for standards document pre-parsers."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Iterable, List, Optional

_KEYWORD_STOPWORDS = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "both",
        "by",
        "can",
        "cannot",
        "do",
        "does",
        "done",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "more",
        "most",
        "no",
        "not",
        "of",
        "off",
        "only",
        "on",
        "or",
        "other",
        "out",
        "over",
        "per",
        "should",
        "such",
        "than",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "up",
        "using",
        "via",
        "was",
        "were",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
    }
)

# Common OCR/layout token fragments observed in parser output.
_KEYWORD_NOISE_TOKENS = frozenset(
    {
        "def",
        "inc",
        "insecur",
        "int",
        "ro",
        "ser",
        "sp",
        "usi",
    }
)

# Conservative static denylist derived from generated-record frequency scans.
# These values currently duplicate framework/version metadata or add little
# retrieval value relative to the explicit metadata fields.
# Revisit later and replace this with generation-time/corpus-aware filtering
# once we have a stable scoring approach for low-information tokens.
_KEYWORD_STATIC_DENYLIST = frozenset(
    {
        "cis",
        "defined",
        "documented",
        "dss",
        "examine",
        "guidelines",
        "pci",
        "requirement",
        "v4",
        "v8",
        "verify",
    }
)


@dataclass
class RequirementRecord:
    """A single normalised requirement extracted from a standards document.

    Designed to be serialised to JSONL and loaded into a dedicated
    Azure AI Search controls index (Corpus A) alongside the main
    evidence corpus (Corpus B).
    """

    # Stable, deterministic identifier.  Format: <framework-slug>-<family-slug>-<scope>-<seq>
    # Example: E8-patch-applications-ML2-003
    requirement_id: str

    # Framework and version metadata
    framework: str  # e.g. "Essential Eight"
    framework_version: str  # e.g. "November 2023"

    # Control classification
    control_family: str  # e.g. "Patch applications"
    maturity_level: Optional[int]  # 1, 2 or 3 for Essential Eight; None for flat frameworks

    # Content
    requirement_text: str  # The normative statement as written in the standard
    guidance_text: str  # Introductory guidance from the corresponding ASD page

    # Search enrichment
    keywords: List[str]  # Derived terms to improve keyword-based retrieval

    # Provenance
    source_uri: str  # Canonical URL of the source page
    source_section: str  # Section name within the source document
    effective_date: str  # Publication or effective date string
    jurisdiction_or_scope: str  # e.g. "Australia"

    def to_dict(self) -> dict:
        """Run to dict."""
        return asdict(self)


class BaseParser(ABC):
    """Abstract base for all standards pre-parsers."""

    @abstractmethod
    def parse(self) -> List[RequirementRecord]:
        """Fetch the source material and return a list of RequirementRecords."""

    def to_jsonl(self, records: List[RequirementRecord]) -> str:
        """Serialise *records* to JSONL (one JSON object per line)."""
        return "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records)


def filter_keywords(keywords: Iterable[str]) -> list[str]:
    """Normalize and remove common stopwords from a keyword iterable."""
    cleaned: list[str] = []
    seen: set[str] = set()

    for keyword in keywords:
        value = str(keyword or "").strip()
        if not value:
            continue

        lowered = value.lower()
        if lowered in _KEYWORD_STOPWORDS:
            continue
        if lowered in _KEYWORD_NOISE_TOKENS:
            continue
        if lowered in _KEYWORD_STATIC_DENYLIST:
            continue

        # Drop pure punctuation/very-short artifacts but keep useful short tags like ML1.
        alnum = re.sub(r"[^a-z0-9]", "", lowered)
        if len(alnum) < 2:
            continue

        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(value)

    return sorted(cleaned, key=lambda item: item.lower())


def keywordise_values(*values: str) -> list[str]:
    """Tokenise free text values and return deduplicated, stopword-filtered tokens."""
    tokens: list[str] = []
    for value in values:
        for token in re.split(r"[^A-Za-z0-9]+", str(value or "").lower()):
            if len(token) >= 2:
                tokens.append(token)
    return filter_keywords(tokens)
