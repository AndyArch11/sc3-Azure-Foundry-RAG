"""Parser for NIST SP 800-53 Rev. 5 using OSCAL catalog/profile XML.

Sources:
- Catalog (controls):
  https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/src/nist.gov/SP800-53/rev5/xml/NIST_SP-800-53_rev5_catalog.xml
- Baseline profiles:
  LOW / MODERATE / HIGH / PRIVACY profile XML files under the same release tag.

The parser emits one RequirementRecord per catalog control (including enhancements)
and maps each control to zero or more baseline profiles when the control ID appears
in the corresponding profile include list.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, List, Set

import requests  # type: ignore[import-untyped]

try:
    from runtime.outbound_instrumentation import request_with_instrumentation
except ImportError:  # Container layout copies modules to /app, not /app/runtime.
    from outbound_instrumentation import request_with_instrumentation

from .base import BaseParser, RequirementRecord, filter_keywords, keywordise_values
from .utils import (
    fetch_bytes_with_instrumentation,
    normalise_space,
    oscal_first_direct_part_text,
    oscal_local_name,
    parse_xml_root,
    slugify_text,
)

logger = logging.getLogger(__name__)

FRAMEWORK = "NIST SP 800-53"
FRAMEWORK_VERSION = "Rev. 5"
EFFECTIVE_DATE = "September 2020"
JURISDICTION = "United States"
SOURCE_URI = "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf"

_OSCAL_NS = {"oscal": "http://csrc.nist.gov/ns/oscal/1.0"}

_OSCAL_CATALOG_URL = (
    "https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/"
    "src/nist.gov/SP800-53/rev5/xml/NIST_SP-800-53_rev5_catalog.xml"
)

_BASELINE_PROFILE_URLS: dict[str, str] = {
    "LOW": (
        "https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/"
        "src/nist.gov/SP800-53/rev5/xml/NIST_SP-800-53_rev5_LOW-baseline_profile.xml"
    ),
    "MODERATE": (
        "https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/"
        "src/nist.gov/SP800-53/rev5/xml/NIST_SP-800-53_rev5_MODERATE-baseline_profile.xml"
    ),
    "HIGH": (
        "https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/"
        "src/nist.gov/SP800-53/rev5/xml/NIST_SP-800-53_rev5_HIGH-baseline_profile.xml"
    ),
    "PRIVACY": (
        "https://raw.githubusercontent.com/usnistgov/oscal-content/refs/tags/v1.4.0/"
        "src/nist.gov/SP800-53/rev5/xml/NIST_SP-800-53_rev5_PRIVACY-baseline_profile.xml"
    ),
}


def _slugify_control_id(control_id: str) -> str:
    """Convert a control ID to a slug suitable for use in requirement IDs.

    Args:
        control_id: The control ID string to slugify.

    Returns:
        The slugified control ID suitable for use in requirement IDs.
    """
    return slugify_text(control_id, delimiter="-").upper()


def _first_direct_part_text(control: ET.Element, part_name: str) -> str:
    """Extract the text of the first direct child part with a given name from a control.

    Args:
        control: The XML element representing the OSCAL control.
        part_name: The name of the part to extract text from.

    Returns:
        The normalised text content of the first matching part, or an empty string if no matching part is found.
    """
    return oscal_first_direct_part_text(control, part_name, namespace=_OSCAL_NS)


def _iter_controls(control: ET.Element) -> Iterable[ET.Element]:
    """Recursively iterate over a control and its child controls.

    Args:
        control: The XML element representing the OSCAL control.
    Yields:
        Each control element, including the input control and its nested child controls.
    """
    yield control
    for child in control.findall("./oscal:control", _OSCAL_NS):
        yield from _iter_controls(child)


def _parse_profile_control_ids(xml_bytes: bytes) -> set[str]:
    """Parse an OSCAL profile XML and extract the set of control IDs included in the profile.

    Args:
        xml_bytes: The XML content of the OSCAL profile as bytes.

    Returns:
        A set of control ID strings included in the profile.
    """
    root = parse_xml_root(xml_bytes)
    ids: set[str] = set()

    for element in root.iter():
        if oscal_local_name(element.tag) != "with-id":
            continue
        value = normalise_space(element.text or "").lower()
        if value:
            ids.add(value)

    return ids


class NistSp80053Parser(BaseParser):
    """Parse NIST SP 800-53 Rev. 5 catalog controls and baseline mappings.

    Attributes:
        _catalog_url: The URL of the OSCAL catalog XML for NIST SP 800-53 Rev. 5.
        _baseline_profile_urls: A mapping of baseline names to their corresponding OSCAL profile XML URLs.
    """

    def __init__(
        self,
        *,
        catalog_url: str = _OSCAL_CATALOG_URL,
        baseline_profile_urls: Dict[str, str] | None = None,
    ) -> None:
        self._catalog_url = catalog_url
        self._baseline_profile_urls = dict(baseline_profile_urls or _BASELINE_PROFILE_URLS)

    def _fetch_xml_bytes(self, *, url: str, operation: str) -> bytes:
        """Fetch XML content from a URL with instrumentation and error handling.

        Args:
            url: The URL to fetch the XML content from.
            operation: A string describing the operation for logging and instrumentation.

        Returns:
            The XML content as bytes.
        Raises:
            requests.HTTPError: If the HTTP request fails or returns a non-success status code.
        """
        return fetch_bytes_with_instrumentation(
            url=url,
            logger=logger,
            timeout=90,
            headers={"User-Agent": "nist-sp-800-53-parser/1.0 (controls ingestion)"},
            system="githubusercontent",
            operation=operation,
            instrumenter=request_with_instrumentation,
            request_callable=requests.get,
        )

    def _load_catalog_root(self) -> ET.Element:
        """Load and parse the OSCAL catalog XML for NIST SP 800-53 Rev. 5.

        Returns:
            The root XML element of the parsed catalog.

        Raises:
            requests.HTTPError: If the HTTP request to fetch the catalog fails.
            xml.etree.ElementTree.ParseError: If the XML content cannot be parsed.
        """
        payload = self._fetch_xml_bytes(
            url=self._catalog_url,
            operation="download_nist_sp_800_53_catalog",
        )
        return parse_xml_root(payload)

    def _load_baseline_map(self) -> dict[str, set[str]]:
        """Load and parse the OSCAL baseline profile XMLs to map control IDs to baselines.

        Returns:
            A dictionary mapping baseline names to sets of control IDs included in each baseline.
        Raises:
            requests.HTTPError: If any HTTP request to fetch a baseline profile fails.
            xml.etree.ElementTree.ParseError: If any baseline profile XML content cannot be parsed.
        """
        baseline_controls: dict[str, set[str]] = {}
        for baseline_name, url in self._baseline_profile_urls.items():
            payload = self._fetch_xml_bytes(
                url=url,
                operation=f"download_nist_sp_800_53_{baseline_name.lower()}_baseline",
            )
            baseline_controls[baseline_name] = _parse_profile_control_ids(payload)
        return baseline_controls

    def parse(self) -> List[RequirementRecord]:
        """Parse the NIST SP 800-53 Rev. 5 catalog and baseline profiles into RequirementRecords.

        Returns:
            A list of RequirementRecord instances representing the parsed controls and their baseline mappings.
        """
        catalog_root = self._load_catalog_root()
        baseline_map = self._load_baseline_map()

        metadata = catalog_root.find("./oscal:metadata", _OSCAL_NS)
        framework_version = FRAMEWORK_VERSION
        effective_date = EFFECTIVE_DATE
        if metadata is not None:
            version_value = normalise_space(
                metadata.findtext("./oscal:version", default="", namespaces=_OSCAL_NS)
            )
            if version_value:
                framework_version = (
                    f"Rev. {version_value}" if version_value.isdigit() else version_value
                )
            modified_value = normalise_space(
                metadata.findtext("./oscal:last-modified", default="", namespaces=_OSCAL_NS)
            )
            if modified_value:
                effective_date = modified_value[:10]

        records: list[RequirementRecord] = []

        for family in catalog_root.findall("./oscal:group", _OSCAL_NS):
            family_title = normalise_space(
                family.findtext("./oscal:title", default="", namespaces=_OSCAL_NS)
            )
            family_id = normalise_space(str(family.attrib.get("id") or "")).upper()
            control_family = family_title or family_id or "General"

            for top_control in family.findall("./oscal:control", _OSCAL_NS):
                for control in _iter_controls(top_control):
                    control_id = normalise_space(str(control.attrib.get("id") or ""))
                    if not control_id:
                        continue

                    statement = _first_direct_part_text(control, "statement")
                    title = normalise_space(
                        control.findtext("./oscal:title", default="", namespaces=_OSCAL_NS)
                    )
                    guidance = _first_direct_part_text(
                        control, "guidance"
                    ) or _first_direct_part_text(control, "discussion")

                    requirement_text = statement or title
                    if not requirement_text:
                        continue

                    control_id_lower = control_id.lower()
                    control_baselines = [
                        baseline_name
                        for baseline_name in ("LOW", "MODERATE", "HIGH", "PRIVACY")
                        if control_id_lower in baseline_map.get(baseline_name, set())
                    ]

                    requirement_id = f"NIST-SP-800-53-{_slugify_control_id(control_id)}"
                    source_section = f"{control_family} > {control_id.upper()}"
                    keywords = filter_keywords(
                        keywordise_values(
                            FRAMEWORK,
                            control_family,
                            control_id,
                            title,
                            " ".join(control_baselines),
                        )
                    )

                    records.append(
                        RequirementRecord(
                            requirement_id=requirement_id,
                            framework=FRAMEWORK,
                            framework_version=framework_version,
                            control_family=control_family,
                            maturity_level=None,
                            requirement_text=requirement_text,
                            guidance_text=guidance,
                            keywords=keywords,
                            source_uri=SOURCE_URI,
                            source_section=source_section,
                            effective_date=effective_date,
                            jurisdiction_or_scope=JURISDICTION,
                            control_baselines=control_baselines,
                        )
                    )

        logger.info("NIST SP 800-53: parsed %d controls", len(records))
        return records
