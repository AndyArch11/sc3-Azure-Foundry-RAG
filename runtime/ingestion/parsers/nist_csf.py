"""Parser for the NIST Cybersecurity Framework (CSF) 2.0 using OSCAL catalog XML.

Primary source:
https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/src/nist.gov/CSF/v2.0/xml/NIST_CSF_v2.0_catalog.xml

This parser emits one RequirementRecord per CSF subcategory control (class="subcategory").
Identifiers and source section formatting preserve the prior static-parser conventions so
existing downstream references remain stable.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import requests  # type: ignore[import-untyped]

try:
    from runtime.outbound_instrumentation import request_with_instrumentation
except ImportError:  # Container layout copies modules to /app, not /app/runtime.
    from outbound_instrumentation import request_with_instrumentation

from .base import BaseParser, RequirementRecord, filter_keywords, keywordise_values
from .utils import (
    fetch_bytes_with_instrumentation,
    normalise_space,
    oscal_all_direct_part_text,
    oscal_first_direct_part_text,
    parse_xml_root,
    slugify_text,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAMEWORK = "NIST CSF"
FRAMEWORK_VERSION = "2.0"
EFFECTIVE_DATE = "2024-02-26"
JURISDICTION = "United States"
SOURCE_URI = "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf"

_OSCAL_CATALOG_URL = (
    "https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/"
    "src/nist.gov/CSF/v2.0/xml/NIST_CSF_v2.0_catalog.xml"
)

_OSCAL_NS = {"oscal": "http://csrc.nist.gov/ns/oscal/1.0"}

# Retained for compatibility with legacy tests that monkeypatch these symbols.
_CSF_CORE: List[Tuple] = []
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Slugify human-readable text using hyphen separators.

    Args:
        text: The input string to slugify.

    Returns:
        A slugified version of the input string, suitable for use in filenames or URLs.
    """
    return slugify_text(text, delimiter="-")


def _normalise_space(value: str) -> str:
    """Collapse whitespace and trim an arbitrary text value.

    Args:
        value: The input string to normalise.

    Returns:
        A string with collapsed whitespace and trimmed edges.
    """
    return normalise_space(value)


def _fetch_guidance(category_id: str) -> str:
    """Best-effort guidance fetch used for trace/header instrumentation validation.

    The CPRT catalog pages are primarily JS-rendered; this call is retained for
    compatibility and observability behavior, but parsing guidance text from this
    endpoint is intentionally not relied upon.

    Args:
        category_id: The NIST CSF category ID to fetch guidance for.

    Returns:
        An empty string, as guidance extraction is not implemented.
    """
    del category_id

    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        logger.debug("beautifulsoup4 not available; skipping NIST CSF guidance fetch")
        return ""

    try:
        payload = fetch_bytes_with_instrumentation(
            url="https://www.nist.gov/cyberframework",
            logger=logger,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            system="nist",
            operation="fetch_nist_csf_guidance",
            instrumenter=request_with_instrumentation,
            request_callable=requests.get,
        )
    except Exception as exc:
        logger.debug("NIST CSF guidance fetch failed: %s", exc)
        return ""

    # Keep a minimal parse operation so call sites exercise bs4 path consistently.
    try:
        BeautifulSoup(payload, "html.parser")
    except Exception:
        return ""
    return ""


def _build_category_guidance_map(fetch_guidance: bool) -> Dict[str, str]:
    """Return an optional category guidance mapping.

    Guidance extraction remains best-effort and currently returns no additional
    category content. The function is preserved for API/test compatibility.

    Args:
        fetch_guidance: Whether to attempt fetching guidance content.

    Returns:
        A dictionary mapping category IDs to guidance text, which is empty in this implementation.
    """
    if not fetch_guidance:
        return {}

    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        logger.warning("requests/beautifulsoup4 not installed; guidance will be empty.")
        return {}

    del BeautifulSoup
    return {}


def _first_direct_part_text(control: ET.Element, part_name: str) -> str:
    """Extract text from the first direct child part with the given name.

    Args:
        control: The XML element representing the control.
        part_name: The name of the part to extract text from.

    Returns:
        The text content of the first matching part, or an empty string if none found.
    """
    return oscal_first_direct_part_text(control, part_name, namespace=_OSCAL_NS)


def _all_direct_part_text(control: ET.Element, part_name: str) -> list[str]:
    """Extract text from all direct child parts with the given name.

    Args:
        control: The XML element representing the control.
        part_name: The name of the part to extract text from.

    Returns:
        A list of text content from all matching parts.
    """
    return oscal_all_direct_part_text(control, part_name, namespace=_OSCAL_NS)


def _humanise_function_title(group_id: str, title: str) -> str:
    """Normalise function heading into readable title case.

    Args:
        group_id: The ID of the function group (e.g., "GV", "ID").
        title: The raw title text to normalise.

    Returns:
        A human-readable title string.
    """
    raw = _normalise_space(title)
    if raw and not raw.isupper():
        return raw

    mapping = {
        "GV": "Govern",
        "ID": "Identify",
        "PR": "Protect",
        "DE": "Detect",
        "RS": "Respond",
        "RC": "Recover",
    }
    return mapping.get(group_id.upper(), raw.title() if raw else group_id.upper())


def _requirement_id(function_id: str, subcategory_id: str) -> str:
    """Build stable requirement IDs matching historical formatting.

    Args:
        function_id: The ID of the function (e.g., "GV", "ID").
        subcategory_id: The ID of the subcategory (e.g., "1.1").

    Returns:
        A string representing the stable requirement ID.
    """
    return f"NIST-CSF-{function_id}-{subcategory_id.replace('.', '-')}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class NistCsfParser(BaseParser):
    """Parser for NIST CSF 2.0 from OSCAL catalog XML.

    Attributes:
        _fetch_guidance: Whether to attempt fetching guidance content.
        _catalog_url: The URL of the OSCAL catalog XML for NIST CSF 2.0.
    """

    def __init__(
        self,
        fetch_guidance: bool = True,
        *,
        catalog_url: str = _OSCAL_CATALOG_URL,
    ) -> None:
        self._fetch_guidance = fetch_guidance
        self._catalog_url = catalog_url

    def _fetch_xml_bytes(self) -> bytes:
        """Fetch the OSCAL catalog XML bytes from the configured URL.

        Returns:
            The raw bytes of the OSCAL catalog XML.

        Raises:
            requests.HTTPError: If the HTTP request to fetch the catalog fails.
        """
        return fetch_bytes_with_instrumentation(
            url=self._catalog_url,
            logger=logger,
            timeout=90,
            headers={"User-Agent": "nist-csf-parser/2.0 (oscal controls ingestion)"},
            system="githubusercontent",
            operation="download_nist_csf_catalog",
            instrumenter=request_with_instrumentation,
            request_callable=requests.get,
        )

    def _load_catalog_root(self) -> ET.Element:
        """Load and parse the OSCAL catalog XML for NIST CSF 2.0.

        Returns:
            The root XML element of the parsed catalog.
        Raises:
            requests.HTTPError: If the HTTP request to fetch the catalog fails.
            xml.etree.ElementTree.ParseError: If the XML content cannot be parsed.
        """
        payload = self._fetch_xml_bytes()
        return parse_xml_root(payload)

    def parse(self) -> List[RequirementRecord]:
        """Parse the NIST CSF 2.0 catalog into RequirementRecords.

        Returns:
            A list of RequirementRecord instances representing the parsed subcategory controls.
        """
        logger.info("Parsing NIST CSF 2.0 catalog (OSCAL)")

        catalog_root = self._load_catalog_root()

        metadata = catalog_root.find("./oscal:metadata", _OSCAL_NS)
        effective_date = EFFECTIVE_DATE
        if metadata is not None:
            modified_value = _normalise_space(
                metadata.findtext("./oscal:last-modified", default="", namespaces=_OSCAL_NS)
            )
            if modified_value:
                effective_date = modified_value[:10]

        guidance_map = _build_category_guidance_map(self._fetch_guidance)

        records: list[RequirementRecord] = []

        for function_group in catalog_root.findall("./oscal:group", _OSCAL_NS):
            function_id = _normalise_space(str(function_group.attrib.get("id") or "")).upper()
            if not function_id:
                continue

            function_title_raw = _normalise_space(
                function_group.findtext("./oscal:title", default="", namespaces=_OSCAL_NS)
            )
            function_title = _humanise_function_title(function_id, function_title_raw)

            for category in function_group.findall("./oscal:control", _OSCAL_NS):
                category_id = _normalise_space(str(category.attrib.get("id") or ""))
                if not category_id:
                    continue

                category_title = _normalise_space(
                    category.findtext("./oscal:title", default="", namespaces=_OSCAL_NS)
                )
                category_statement = _first_direct_part_text(category, "statement")
                category_guidance = guidance_map.get(category_id, "") or category_statement

                control_family = (
                    f"{category_id} - {category_title}" if category_title else category_id
                )
                source_section = (
                    f"{function_title} ({function_id}) > {category_title} ({category_id})"
                )

                for subcategory in category.findall("./oscal:control", _OSCAL_NS):
                    subcategory_id = _normalise_space(str(subcategory.attrib.get("id") or ""))
                    if not subcategory_id:
                        continue

                    subcategory_title = _normalise_space(
                        subcategory.findtext("./oscal:title", default="", namespaces=_OSCAL_NS)
                    )
                    subcategory_statement = _first_direct_part_text(subcategory, "statement")
                    if not subcategory_statement and not subcategory_title:
                        continue

                    requirement_text = (
                        f"[{subcategory_id}] {subcategory_statement or subcategory_title}"
                    )

                    examples = _all_direct_part_text(subcategory, "example")
                    guidance_segments = [segment for segment in [category_guidance] if segment]
                    if examples:
                        guidance_segments.append("Examples: " + " | ".join(examples))
                    guidance_text = "\n\n".join(guidance_segments)

                    risk_party_values = [
                        _normalise_space(str(prop.attrib.get("value") or ""))
                        for prop in subcategory.findall("./oscal:prop", _OSCAL_NS)
                        if _normalise_space(str(prop.attrib.get("name") or "")) == "risk-party"
                    ]

                    keywords = filter_keywords(
                        keywordise_values(
                            FRAMEWORK,
                            function_id,
                            function_title,
                            category_id,
                            category_title,
                            subcategory_id,
                            subcategory_statement,
                            " ".join(risk_party_values),
                        )
                    )

                    records.append(
                        RequirementRecord(
                            requirement_id=_requirement_id(function_id, subcategory_id),
                            framework=FRAMEWORK,
                            framework_version=FRAMEWORK_VERSION,
                            control_family=control_family,
                            maturity_level=None,
                            requirement_text=requirement_text,
                            guidance_text=guidance_text,
                            keywords=keywords,
                            source_uri=SOURCE_URI,
                            source_section=source_section,
                            effective_date=effective_date,
                            jurisdiction_or_scope=JURISDICTION,
                        )
                    )

        logger.info("Produced %d NIST CSF 2.0 subcategory records", len(records))
        return records
