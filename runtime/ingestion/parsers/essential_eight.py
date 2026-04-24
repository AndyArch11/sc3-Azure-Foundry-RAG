"""Pre-parser for the ASD Essential Eight Maturity Model.

Fetches the canonical maturity model page and the five supplementary ASD
guidance pages, then emits one RequirementRecord per normative statement
found in Appendix A (ML1), B (ML2) and C (ML3) of the model.

Requirements are not deduplicated across levels - an organisation assessing
ML2 compliance needs ALL requirements that appear in the ML2 appendix,
including those carried forward from ML1.  Filtering by ``maturity_level``
therefore returns the complete requirement set for that level.

Usage::

    from runtime.ingestion.parsers.essential_eight import EssentialEightParser
    parser = EssentialEightParser()
    records = parser.parse()
    print(parser.to_jsonl(records))
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from .base import BaseParser, RequirementRecord, filter_keywords

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MATURITY_MODEL_URL = (
    "https://www.cyber.gov.au/business-government/asds-cyber-security-frameworks"
    "/essential-eight/essential-eight-maturity-model"
)

FRAMEWORK = "Essential Eight"
FRAMEWORK_VERSION = "November 2023"
EFFECTIVE_DATE = "November 2023"
JURISDICTION = "Australia"

# Canonical control family names as they appear in the ASD standard.
CONTROL_FAMILIES: List[str] = [
    "Patch applications",
    "Patch operating systems",
    "Multi-factor authentication",
    "Restrict administrative privileges",
    "Application control",
    "Restrict Microsoft Office macros",
    "User application hardening",
    "Regular backups",
]

# Search-enrichment keywords per control family.
FAMILY_KEYWORDS: Dict[str, List[str]] = {
    "Patch applications": [
        "patching",
        "vulnerability",
        "patch management",
        "applications",
        "updates",
        "critical patches",
        "vulnerability scanner",
    ],
    "Patch operating systems": [
        "patching",
        "vulnerability",
        "operating system",
        "OS",
        "updates",
        "critical patches",
        "vulnerability scanner",
    ],
    "Multi-factor authentication": [
        "MFA",
        "multi-factor authentication",
        "phishing-resistant",
        "identity",
        "authentication",
        "security key",
    ],
    "Restrict administrative privileges": [
        "privileged accounts",
        "administrative privileges",
        "admin",
        "least privilege",
        "administrative access",
        "privileged access",
    ],
    "Application control": [
        "application control",
        "allowlisting",
        "WDAC",
        "AppLocker",
        "execution control",
        "malware prevention",
    ],
    "Restrict Microsoft Office macros": [
        "macros",
        "VBA",
        "Office macros",
        "macro security",
        "Microsoft Office",
        "trusted publisher",
    ],
    "User application hardening": [
        "hardening",
        "web browser",
        "PDF software",
        "Flash",
        "security configuration",
        "user applications",
    ],
    "Regular backups": [
        "backup",
        "recovery",
        "business continuity",
        "data protection",
        "restore",
        "offline backups",
    ],
}

# URLs of supplementary ASD guidance pages, keyed by canonical control family name.
GUIDANCE_URLS: Dict[str, str] = {
    "Patch applications": (
        "https://www.cyber.gov.au/business-government/protecting-devices-systems"
        "/system-administration/patching-applications-and-operating-systems"
    ),
    "Patch operating systems": (
        "https://www.cyber.gov.au/business-government/protecting-devices-systems"
        "/system-administration/patching-applications-and-operating-systems"
    ),
    "Multi-factor authentication": (
        "https://www.cyber.gov.au/business-government/protecting-devices-systems"
        "/hardening-systems-applications/system-hardening"
        "/implementing-multi-factor-authentication"
    ),
    "Restrict administrative privileges": (
        "https://www.cyber.gov.au/business-government/protecting-devices-systems"
        "/system-administration/restricting-administrative-privileges"
    ),
    "Application control": (
        "https://www.cyber.gov.au/business-government/protecting-devices-systems"
        "/hardening-systems-applications/system-hardening"
        "/implementing-application-control"
    ),
    "Restrict Microsoft Office macros": (
        "https://www.cyber.gov.au/business-government/protecting-devices-systems"
        "/hardening-systems-applications/system-hardening"
        "/restricting-microsoft-office-macros"
    ),
}

# Appendix letter → (maturity_level, human-readable section name)
APPENDIX_MAP: Dict[str, tuple] = {
    "a": (1, "Appendix A \u2013 Maturity Level One"),
    "b": (2, "Appendix B \u2013 Maturity Level Two"),
    "c": (3, "Appendix C \u2013 Maturity Level Three"),
}

# Header cell values that should be skipped when encountered in a table row.
_HEADER_CELL_VALUES = frozenset(
    {
        "mitigation strategy",
        "maturity level one",
        "maturity level two",
        "maturity level three",
        "maturity level 1",
        "maturity level 2",
        "maturity level 3",
        "level 1",
        "level 2",
        "level 3",
        "one",
        "two",
        "three",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert *text* to a lowercase hyphen-delimited slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def _normalise_family_name(raw: str) -> Optional[str]:
    """Match *raw* text to one of the canonical control family names."""
    raw_clean = raw.strip()
    if not raw_clean:
        return None

    # Exact match (case-insensitive)
    for canonical in CONTROL_FAMILIES:
        if canonical.lower() == raw_clean.lower():
            return canonical

    # Prefix/substring match for robustness (e.g. truncated cell text)
    for canonical in CONTROL_FAMILIES:
        if raw_clean.lower().startswith(canonical[:8].lower()):
            return canonical

    return None


def _fetch_soup(url: str):
    """Fetch *url* and return a BeautifulSoup parse tree."""
    try:
        import requests  # noqa: PLC0415  # type: ignore
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "requests and beautifulsoup4 are required by the parsers package. "
            "Install them with: pip install requests beautifulsoup4"
        ) from exc

    logger.debug("Fetching %s", url)
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _extract_introduction(soup) -> str:
    """Return the prose text of the Introduction section of a guidance page."""
    intro_heading = None
    for tag in soup.find_all(["h2", "h3", "h4"]):
        if "introduction" in tag.get_text(strip=True).lower():
            intro_heading = tag
            break

    if not intro_heading:
        return ""

    heading_level = int(intro_heading.name[1])
    stop_names = [f"h{i}" for i in range(1, heading_level + 1)]

    parts: List[str] = []
    sibling = intro_heading.find_next_sibling()
    while sibling:
        if sibling.name in stop_names:
            break
        if sibling.name == "p":
            text = sibling.get_text(" ", strip=True)
            if text:
                parts.append(text)
        elif sibling.name in ("ul", "ol"):
            for li in sibling.find_all("li", recursive=False):
                text = li.get_text(" ", strip=True)
                if text:
                    parts.append(f"\u2022 {text}")
        sibling = sibling.find_next_sibling()

    return " ".join(parts)


def _extract_cell_requirements(cell) -> List[str]:
    """Extract individual requirement statements embedded in a single <td>."""
    # Prefer <li> items (most explicit boundary)
    li_items = cell.find_all("li")
    if li_items:
        return [li.get_text(" ", strip=True) for li in li_items if li.get_text(strip=True)]

    # Fall back to <p> elements
    p_items = cell.find_all("p")
    if p_items:
        return [p.get_text(" ", strip=True) for p in p_items if p.get_text(strip=True)]

    # Last resort: split on newlines / bullet characters
    full_text = cell.get_text("\n", strip=True)
    reqs: List[str] = []
    for line in full_text.splitlines():
        line = line.lstrip("\u2022\u00b7\u2013-").strip()
        if line and len(line) > 5:
            reqs.append(line)
    return reqs


def _parse_requirement_table(
    table,
    maturity_level: int,
    source_uri: str,
    source_section: str,
    guidance_map: Dict[str, str],
) -> List[RequirementRecord]:
    """Parse a single HTML table into RequirementRecord objects."""
    records: List[RequirementRecord] = []
    current_family: Optional[str] = None
    family_counters: Dict[str, int] = {}

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        if len(cells) >= 2:
            first_text = cells[0].get_text(" ", strip=True)
            # Skip header rows
            if first_text.lower() in _HEADER_CELL_VALUES:
                continue
            normalised = _normalise_family_name(first_text)
            if normalised:
                current_family = normalised
            req_cell = cells[1]

        elif len(cells) == 1:
            # Continuation row (rowspan on first cell)
            req_cell = cells[0]

        else:
            continue

        if not current_family:
            continue

        # Skip header-like content in the requirement column
        req_col_text = req_cell.get_text(" ", strip=True)
        if not req_col_text or req_col_text.lower() in _HEADER_CELL_VALUES:
            continue

        req_texts = _extract_cell_requirements(req_cell)
        for req_text in req_texts:
            req_text = req_text.strip()
            if not req_text or len(req_text) < 10:
                continue
            # Skip anything that reads like a column header
            if req_text.lower() in _HEADER_CELL_VALUES:
                continue

            family_counters.setdefault(current_family, 0)
            family_counters[current_family] += 1
            seq = family_counters[current_family]

            records.append(
                RequirementRecord(
                    requirement_id=(f"E8-{_slugify(current_family)}-ML{maturity_level}-{seq:03d}"),
                    framework=FRAMEWORK,
                    framework_version=FRAMEWORK_VERSION,
                    control_family=current_family,
                    maturity_level=maturity_level,
                    requirement_text=req_text,
                    guidance_text=guidance_map.get(current_family, ""),
                    keywords=filter_keywords(FAMILY_KEYWORDS.get(current_family, [])),
                    source_uri=source_uri,
                    source_section=source_section,
                    effective_date=EFFECTIVE_DATE,
                    jurisdiction_or_scope=JURISDICTION,
                )
            )

    return records


def _parse_maturity_model_page(
    soup,
    source_url: str,
    guidance_map: Dict[str, str],
) -> List[RequirementRecord]:
    """Extract all requirement records from the maturity model HTML page."""
    # Locate the main article body (several selectors tried in order)
    content = (
        soup.find("div", class_=lambda c: c and "field--name-body" in c)
        or soup.find("main")
        or soup.find("article")
        or soup
    )

    records: List[RequirementRecord] = []
    current_appendix_level: Optional[int] = None
    current_section_name: Optional[str] = None

    for element in content.find_all(["h1", "h2", "h3", "h4", "h5", "table"]):
        if element.name in ("h1", "h2", "h3", "h4", "h5"):
            heading_text = element.get_text(strip=True).lower()

            matched = False
            for letter, (level, section_name) in APPENDIX_MAP.items():
                if f"appendix {letter}" in heading_text:
                    current_appendix_level = level
                    current_section_name = section_name
                    matched = True
                    break

            if not matched:
                # Appendix D (comparison) or any other section ends the requirement tables
                if "appendix d" in heading_text or (
                    current_appendix_level is not None
                    and element.name in ("h1", "h2")
                    and not any(f"appendix {l}" in heading_text for l in APPENDIX_MAP)
                ):
                    current_appendix_level = None
                    current_section_name = None

        elif element.name == "table" and current_appendix_level is not None:
            table_records = _parse_requirement_table(
                element,
                current_appendix_level,
                source_url,
                current_section_name or "",
                guidance_map,
            )
            records.extend(table_records)

    return records


# ---------------------------------------------------------------------------
# Public parser classes
# ---------------------------------------------------------------------------


class ASDGuidanceParser:
    """Fetches a single ASD supplementary guidance page and extracts its
    introduction text, to be embedded as ``guidance_text`` in requirement records.
    """

    def __init__(self, control_family: str, url: str) -> None:
        """Run init."""
        self.control_family = control_family
        self.url = url

    def fetch_introduction(self) -> str:
        """Return the introduction prose from the guidance page, or empty string on error."""
        try:
            soup = _fetch_soup(self.url)
            return _extract_introduction(soup)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not fetch guidance for '%s' from %s: %s",
                self.control_family,
                self.url,
                exc,
            )
            return ""


class EssentialEightParser(BaseParser):
    """Parses the ASD Essential Eight Maturity Model page into individual
    ``RequirementRecord`` objects, one per normative statement per maturity level.

    Optionally enriches each record's ``guidance_text`` field with the
    introduction section from the corresponding supplementary ASD guidance page.

    Args:
        maturity_model_url: URL of the main maturity model page.
        fetch_guidance: When *True* (default), fetch each supplementary
            guidance page and embed its introduction text.
    """

    def __init__(
        self,
        maturity_model_url: str = MATURITY_MODEL_URL,
        fetch_guidance: bool = True,
    ) -> None:
        """Run init."""
        self.maturity_model_url = maturity_model_url
        self.fetch_guidance = fetch_guidance

    def parse(self) -> List[RequirementRecord]:
        # 1. Optionally build a guidance_text map keyed by control family.
        """Run parse."""
        guidance_map: Dict[str, str] = {}
        if self.fetch_guidance:
            seen_urls: Dict[str, str] = {}  # url -> already-fetched guidance text
            for family, url in GUIDANCE_URLS.items():
                if url in seen_urls:
                    guidance_map[family] = seen_urls[url]
                else:
                    text = ASDGuidanceParser(family, url).fetch_introduction()
                    guidance_map[family] = text
                    seen_urls[url] = text

        # 2. Fetch and parse the main maturity model page.
        soup = _fetch_soup(self.maturity_model_url)
        records = _parse_maturity_model_page(soup, self.maturity_model_url, guidance_map)

        if not records:
            logger.warning(
                "No requirement records extracted from %s. " "The page structure may have changed.",
                self.maturity_model_url,
            )
        else:
            logger.info(
                "Extracted %d requirement records from Essential Eight maturity model.",
                len(records),
            )

        return records
