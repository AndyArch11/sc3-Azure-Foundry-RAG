"""
Control Applicability Classification Module.

This module provides functionality to classify controls as technical, process, governance, or mixed.
It includes regex patterns for identifying technical and process controls, as well as governance ID patterns.
The classification is based on the control's requirement ID and text content, and returns metadata including confidence scores and matching details.
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
    """Applicability classification for a single control.

    Attributes:
        scope: The applicability scope of the control ("technical", "process", "governance", or "mixed").
        confidence: The confidence score of the classification (0.0-1.0).
        technical_matches: The number of technical matches found in the control text.
        process_matches: The number of process matches found in the control text.
        governance_id_match: True if the control's requirement ID matches a known governance pattern.
        uncertain: True if the confidence score is below 0.70, indicating uncertainty in the classification.
    """

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

    Args:
        control: A dictionary representing the control to classify.
    Returns:
        A ControlApplicabilityMetadata object containing the classification results.
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

    Args:
        control: A dictionary representing the control to enrich.
    Returns:
        The same control dictionary with added applicability metadata fields:
            - control_applicability_scope
            - applicability_confidence
            - applicability_uncertain
    """
    metadata = classify_control_applicability(control)
    control["control_applicability_scope"] = metadata.scope
    control["applicability_confidence"] = round(metadata.confidence, 3)
    control["applicability_uncertain"] = metadata.uncertain
    return control
