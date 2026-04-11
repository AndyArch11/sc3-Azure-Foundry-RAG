"""
Control applicability classification — shared logic for deterministic scope and confidence scoring.
Used at ingestion time to enrich controls, and at runtime for filtering decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_AZURE_TECHNICAL_CONTROL_RE = re.compile(
    r"\b(mfa|multi-factor|authentication|access control|least privilege|rbac|network|firewall|segment|encrypt|encryption|key management|tls|certificate|logging|monitor|alert|backup|restore|patch|vulnerab|malware|endpoint|hardening|configuration|baseline|disable|enable|restrict|private endpoint|managed identity|secret|key vault|diagnostic|defender|inventory|discover|secure transfer|immutability|retention|deny|auditifnotexists|deployifnotexists|modify)\b",
    re.IGNORECASE,
)
_AZURE_PROCESS_CONTROL_RE = re.compile(
    r"\b(policy(?!\s+assignment)|policies|procedure|procedures|governance|strategy|roadmap|roles?\s+and\s+responsibilit|training|awareness|exercise|tabletop|legal|regulatory|compliance\s+program|audit\b|vendor|supplier|third[-\s]?party|personnel|workforce|human resources|continuity|recovery plan|communication plan|approve|approval|document(?:ed|ation)?|review cadence|oversight|charter|committee|budget|insurance|procurement)\b",
    re.IGNORECASE,
)
_AZURE_GOVERNANCE_ID_RE = re.compile(r"^(GV(?:\.|-)|ID\.GV\b|AT-\d+|PM-\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ControlApplicabilityMetadata:
    """Applicability classification for a single control."""

    scope: str  # "technical" | "process" | "governance" | "mixed"
    confidence: float  # 0.0-1.0
    technical_matches: int
    process_matches: int
    governance_id_match: bool
    uncertain: bool  # True if confidence < 0.70


def classify_control_applicability(control: dict[str, Any]) -> ControlApplicabilityMetadata:
    """
    Classify a control as technical, process, governance, or mixed.
    Returns metadata including confidence score and matching details.
    """
    requirement_id = str(control.get("requirement_id") or "").strip()

    text = "\n".join(
        str(control.get(field) or "")
        for field in ("control_family", "requirement_text", "guidance_text")
    )

    governance_id_match = bool(requirement_id and _AZURE_GOVERNANCE_ID_RE.search(requirement_id))
    technical_matches = len(_AZURE_TECHNICAL_CONTROL_RE.findall(text))
    process_matches = len(_AZURE_PROCESS_CONTROL_RE.findall(text))

    if governance_id_match:
        scope = "governance"
        confidence = 0.98
    elif process_matches > 0 and technical_matches == 0:
        scope = "process"
        confidence = 0.90
    elif technical_matches > 0 and process_matches == 0:
        scope = "technical"
        confidence = 0.92
    elif technical_matches > 0 and process_matches > 0:
        ratio = technical_matches / (technical_matches + process_matches)
        if ratio > 0.7:
            scope = "technical"
            confidence = 0.70
        elif ratio < 0.3:
            scope = "process"
            confidence = 0.65
        else:
            scope = "mixed"
            confidence = 0.55
    else:
        scope = "mixed"
        confidence = 0.50

    return ControlApplicabilityMetadata(
        scope=scope,
        confidence=confidence,
        technical_matches=technical_matches,
        process_matches=process_matches,
        governance_id_match=governance_id_match,
        uncertain=confidence < 0.70,
    )


def enrich_control_with_applicability(control: dict[str, Any]) -> dict[str, Any]:
    """
    Enrich a control document with applicability metadata.
    Mutates the input dict and returns it.
    """
    metadata = classify_control_applicability(control)
    control["control_applicability_scope"] = metadata.scope
    control["applicability_confidence"] = round(metadata.confidence, 3)
    control["applicability_uncertain"] = metadata.uncertain
    return control
