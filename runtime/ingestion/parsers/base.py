"""Base classes for standards document pre-parsers."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import List, Optional


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
    framework: str               # e.g. "Essential Eight"
    framework_version: str       # e.g. "November 2023"

    # Control classification
    control_family: str          # e.g. "Patch applications"
    maturity_level: Optional[int]  # 1, 2 or 3 for Essential Eight; None for flat frameworks

    # Content
    requirement_text: str        # The normative statement as written in the standard
    guidance_text: str           # Introductory guidance from the corresponding ASD page

    # Search enrichment
    keywords: List[str]          # Derived terms to improve keyword-based retrieval

    # Provenance
    source_uri: str              # Canonical URL of the source page
    source_section: str          # Section name within the source document
    effective_date: str          # Publication or effective date string
    jurisdiction_or_scope: str   # e.g. "Australia"

    def to_dict(self) -> dict:
        return asdict(self)


class BaseParser(ABC):
    """Abstract base for all standards pre-parsers."""

    @abstractmethod
    def parse(self) -> List[RequirementRecord]:
        """Fetch the source material and return a list of RequirementRecords."""

    def to_jsonl(self, records: List[RequirementRecord]) -> str:
        """Serialise *records* to JSONL (one JSON object per line)."""
        return "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records)
