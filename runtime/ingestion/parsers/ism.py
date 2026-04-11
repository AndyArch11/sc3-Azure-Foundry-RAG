"""Parser for the Australian Signals Directorate Information Security Manual (ISM).

Source: ISM OSCAL catalog published by ASD / Australian Cyber Security Centre.
The catalog is fetched at parse time from the official GitHub mirror:
    https://github.com/AustralianCyberSecurityCentre/ism-oscal

The ISM is structured as numbered controls (e.g. ISM-1997) across ~23 guideline
chapters plus a set of labelled cyber security principles (GOV-01, etc.).
Controls are emitted as one ``RequirementRecord`` per OSCAL control (1 000+).

Usage::

    from runtime.ingestion.parsers.ism import IsmParser
    records = IsmParser().parse()
    print(len(records))  # 1130+ depending on ISM version
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Dict, List, Optional, Tuple

from .base import BaseParser, RequirementRecord, filter_keywords

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAMEWORK = "ISM"
EFFECTIVE_DATE = "March 2026"
JURISDICTION = "Australia"

# The GitHub raw URL always points to the latest release on main branch.
_OSCAL_CATALOG_URL = (
    "https://raw.githubusercontent.com/"
    "AustralianCyberSecurityCentre/ism-oscal/main/ISM_catalog.json"
)

# Source URL shown in provenance
SOURCE_URI = "https://www.cyber.gov.au/business-government/asds-cyber-security-frameworks/ism"

# Applicability code → human label used in keywords
_APPLICABILITY_LABELS: Dict[str, str] = {
    "NC": "non-classified",
    "OS": "OFFICIAL Sensitive",
    "P": "PROTECTED",
    "S": "SECRET",
    "TS": "TOP SECRET",
    "E8ML1": "Essential Eight ML1",
    "E8ML2": "Essential Eight ML2",
    "E8ML3": "Essential Eight ML3",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert arbitrary text to a lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _requirement_id(oscal_id: str) -> str:
    """Derive a stable requirement_id from an OSCAL control id.

    Examples:
        ``ism-1997``               → ``ISM-1997``
        ``ism-principle-gov-01``   → ``ISM-GOV-01``
    """
    # Principles have the pattern ism-principle-<label-lowercase>
    # e.g. ism-principle-gov-01 → GOV-01
    principle_match = re.match(r"ism-principle-([a-z0-9-]+)", oscal_id)
    if principle_match:
        label_part = principle_match.group(1).upper()  # e.g. GOV-01
        return f"ISM-{label_part}"

    # Numbered controls: ism-NNNN
    number_match = re.match(r"ism-(\d+)$", oscal_id)
    if number_match:
        return f"ISM-{number_match.group(1)}"

    # Fallback: sanitise as-is
    return f"ISM-{oscal_id.upper().lstrip('ISM-')}"


def _extract_statement(control: dict) -> str:
    """Return the statement prose from a control's parts list."""
    for part in control.get("parts", []):
        if part.get("name") == "statement":
            return part.get("prose", "").strip()
    return ""


def _collect_controls(
    group: dict,
    guideline: str,
    section: str,
    results: List[Tuple[dict, str, str, str]],
) -> None:
    """Recursively walk OSCAL groups and collect (control, guideline, section, subsection)."""
    title = group.get("title", "")

    for control in group.get("controls", []):
        results.append((control, guideline, section, title))

    for subgroup in group.get("groups", []):
        if not guideline:
            # Top-level group below catalog root → this becomes the guideline
            _collect_controls(subgroup, guideline=title, section="", results=results)
        elif not section:
            _collect_controls(subgroup, guideline=guideline, section=title, results=results)
        else:
            # Deeper nesting → treat as subsection variation; keep guideline/section
            _collect_controls(subgroup, guideline=guideline, section=section, results=results)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class IsmParser(BaseParser):
    """Parse the ASD Information Security Manual from its OSCAL catalog.

    Parameters
    ----------
    catalog_url:
        URL to the ISM OSCAL JSON catalog. Defaults to the GitHub mirror main
        branch, which always reflects the latest published release.
    """

    def __init__(self, catalog_url: str = _OSCAL_CATALOG_URL, **_kwargs) -> None:
        self._catalog_url = catalog_url

    # ------------------------------------------------------------------
    # BaseParser implementation
    # ------------------------------------------------------------------

    def parse(self) -> List[RequirementRecord]:
        logger.info("Fetching ISM OSCAL catalog from %s", self._catalog_url)
        catalog_data = self._fetch_catalog()
        return self._build_records(catalog_data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_catalog(self) -> dict:
        req = urllib.request.Request(
            self._catalog_url,
            headers={"User-Agent": "ism-parser/1.0 (controls ingestion)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read()
        return json.loads(raw)

    def _build_records(self, data: dict) -> List[RequirementRecord]:
        catalog = data.get("catalog", data)
        meta = catalog.get("metadata", {})

        # Use the catalog version if available, otherwise fallback constant
        catalog_version: str = meta.get("version", EFFECTIVE_DATE)
        effective_date: str = meta.get("last-modified", EFFECTIVE_DATE)[:10]

        groups = catalog.get("groups", [])

        # Collect all controls with their guideline/section/subsection context
        raw_controls: List[Tuple[dict, str, str, str]] = []
        for group in groups:
            _collect_controls(group, guideline="", section="", results=raw_controls)

        records: List[RequirementRecord] = []
        for control, guideline, section, subsection in raw_controls:
            oscal_id: str = control.get("id", "")
            if not oscal_id:
                continue

            # ------------------------------------------------------------------
            # Extract props
            # ------------------------------------------------------------------
            props: Dict[str, object] = {}
            applicability: List[str] = []
            for prop in control.get("props", []):
                name = prop.get("name")
                value = prop.get("value")
                if name == "applicability":
                    applicability.append(str(value))
                elif name and value is not None:
                    props[name] = value

            # ------------------------------------------------------------------
            # Build fields
            # ------------------------------------------------------------------
            requirement_id = _requirement_id(oscal_id)
            statement = _extract_statement(control)

            # Use control title as guidance_text; strip generic "Control: ism-NNNN" prefix
            raw_title: str = control.get("title", "")
            if re.match(r"Control:\s+ism-\d+$", raw_title, re.IGNORECASE):
                guidance_text = ""
            else:
                guidance_text = raw_title

            # Derive source section path
            parts = [p for p in (guideline, section, subsection) if p]
            source_section = " > ".join(parts) if parts else guideline

            # Keywords: section path words + applicability labels
            keyword_parts = [guideline, section, subsection]
            keywords = list(
                {
                    w.lower()
                    for chunk in keyword_parts
                    for w in re.split(r"\W+", chunk)
                    if len(w) > 2
                }
            )
            for code in applicability:
                label = _APPLICABILITY_LABELS.get(code)
                if label:
                    keywords.append(label)
            keywords = filter_keywords(keywords)

            records.append(
                RequirementRecord(
                    requirement_id=requirement_id,
                    framework=FRAMEWORK,
                    framework_version=catalog_version,
                    control_family=guideline or "General",
                    maturity_level=None,
                    requirement_text=statement,
                    guidance_text=guidance_text,
                    keywords=keywords,
                    source_uri=SOURCE_URI,
                    source_section=source_section,
                    effective_date=effective_date,
                    jurisdiction_or_scope=JURISDICTION,
                )
            )

        logger.info("ISM: parsed %d controls (version %s)", len(records), catalog_version)
        return records
