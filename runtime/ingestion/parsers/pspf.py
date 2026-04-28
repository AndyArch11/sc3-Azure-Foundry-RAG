"""Parser for the Australian Government Protective Security Policy Framework (PSPF).

Source documents:
    https://www.protectivesecurity.gov.au/pspf-annual-release
    https://www.protectivesecurity.gov.au/system/files/2025-07/pspf-release-2025.pdf

The release PDF contains the authoritative numbered PSPF requirements and the
section-level guidance text that introduces each requirement group.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, List

import requests  # type: ignore[import-untyped]

from runtime.outbound_instrumentation import request_with_instrumentation

from .base import BaseParser, RequirementRecord, keywordise_values

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfReader = None  # type: ignore[assignment,misc]

FRAMEWORK = "PSPF"
FRAMEWORK_VERSION = "Release 2025"
EFFECTIVE_DATE = "July 2025"
JURISDICTION = "Australia"
ANNUAL_RELEASE_URI = "https://www.protectivesecurity.gov.au/pspf-annual-release"
SOURCE_URI = "https://www.protectivesecurity.gov.au/system/files/2025-07/pspf-release-2025.pdf"

_DOMAIN_NAMES = {
    "GOV": "Governance",
    "RISK": "Risk",
    "INFO": "Information",
    "TECH": "Technology",
    "PER": "Personnel",
    "PHY": "Physical",
}

_DOMAIN_KEYWORDS = {
    "GOV": ["governance", "policy", "oversight", "accountability"],
    "RISK": ["risk", "risk management", "assurance", "mitigation"],
    "INFO": ["information", "classification", "sharing", "handling"],
    "TECH": ["technology", "cyber", "systems", "infrastructure"],
    "PER": ["personnel", "vetting", "clearance", "workforce"],
    "PHY": ["physical security", "facilities", "zones", "access control"],
}

_APPLICABILITY_KEYWORDS = {
    "all entities": ["entity"],
    "department of state": ["dos"],
    "technical authority entity": ["tae"],
    "shared service provider entity": ["sspe"],
    "authorised vetting agency": ["ava"],
    "system of government significance": ["sogs"],
}

_CONTROL_KEYWORDS = {
    "departments of state": ["dos", "portfolio support", "government security advice"],
    "technical authority entities": ["tae", "technical standards", "security advice"],
    "shared service provider entities": ["sspe", "shared services", "service arrangements"],
    "chief security officer": ["cso", "security leadership", "protective security"],
    "chief information security officer": ["ciso", "cyber leadership", "cyber program"],
    "security governance": ["governance committee", "executive oversight"],
    "security incidents": ["incident response", "incident reporting", "security event"],
    "externally reportable security incidents and referral obligations": [
        "incident reporting",
        "referral obligations",
        "reportable incidents",
    ],
    "security investigations": ["investigations", "misconduct", "security inquiry"],
    "security classifications": ["official", "protected", "secret", "top secret"],
    "information security manual": ["ism", "asd", "cyber controls"],
    "technology system authorisation": [
        "authorisation to operate",
        "authorising officer",
        "security assessor",
    ],
    "technology system reauthorisation": [
        "reauthorisation",
        "authorisation review",
        "residual risk",
    ],
    "secure cloud": ["cloud", "irap", "cloud hosting"],
    "gateway security": ["gateway", "security service edge", "sse"],
    "vulnerability disclosure program": [
        "vdp",
        "coordinated disclosure",
        "vulnerability reporting",
    ],
    "cyber security partnership program": ["acsc", "partnership program", "cyber collaboration"],
    "cyber threat intelligence sharing platform": [
        "ctis",
        "threat intelligence",
        "machine-speed sharing",
    ],
    "systems of government significance": [
        "sogs",
        "critical digital services",
        "government significance",
    ],
    "security clearances": ["security clearance", "vetting", "clearance eligibility"],
    "authorised vetting agencies": ["ava", "vetting authority", "security clearance"],
    "eligibility waivers": ["waiver", "citizenship waiver", "background waiver"],
    "security clearance revalidation": ["revalidation", "periodic review", "clearance maintenance"],
    "ongoing access to resources": ["need-to-know", "access control", "authorised access"],
    "temporary access to resources": ["temporary access", "visitor access", "short-term access"],
    "working remotely in australia": ["remote work", "telework", "work from home"],
    "working remotely outside of australia (international)": [
        "international travel",
        "overseas remote work",
        "foreign travel",
    ],
    "physical security measures and controls": [
        "security zones",
        "physical protections",
        "facility controls",
    ],
    "security zone certification authorities": [
        "certification authority",
        "zone certification",
        "physical accreditation",
    ],
    "security zone accreditation authorities": [
        "accreditation authority",
        "security zone",
        "physical accreditation",
    ],
    "tiktok application": ["tiktok", "social media", "mobile application risk"],
    "quantum computing": ["post-quantum", "pqc", "cryptography"],
    "patch applications": ["patching", "vulnerability", "application updates"],
    "patch operating systems": ["patching", "operating system", "os updates"],
    "multi-factor authentication": ["mfa", "phishing-resistant", "strong authentication"],
    "restrict administrative privileges": [
        "least privilege",
        "privileged access",
        "privileged accounts",
    ],
    "application control": ["allowlisting", "wdac", "applocker"],
    "restrict microsoft office macro settings": ["macro security", "vba", "office macros"],
    "user application hardening": [
        "browser hardening",
        "application hardening",
        "security configuration",
    ],
    "regular backups": ["backup", "restore", "recovery"],
}

_REQ_HEADER_RE = re.compile(
    r"^Requirement\s+(?P<req>\d{4})\s*\|\s*(?P<domain>[A-Z]+)\s*\|\s*"
    r"(?P<applicability>.*?)\s*\|\s*(?P<start_date>\d{1,2}\s+[A-Za-z]+\s+\d{4})$"
)
_HEADING_RE = re.compile(r"^(?P<number>\d{1,2}(?:\.\d+){0,2})\s+(?P<title>[A-Z][^\n]+?)$")
_HEADER_RE = re.compile(
    r"Australian Government\s+Protective Security Policy Framework\s+Release 2025", re.IGNORECASE
)
_ROMAN_OR_PAGE_RE = re.compile(r"^(?:[ivxlcdm]+|\d+)$", re.IGNORECASE)


def _flatten(text: str) -> str:
    """Run flatten."""
    text = text.replace("-\n", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_page_text(text: str) -> str:
    """Run clean page text."""
    cleaned: list[str] = []
    for raw_line in (text or "").replace("\xa0", " ").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line.lower() == "protectivesecurity.gov.au":
            continue
        if _HEADER_RE.search(line):
            continue
        if _ROMAN_OR_PAGE_RE.match(line):
            continue
        if line in {"Australian Government", "Contents"}:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _download_pdf_bytes(url: str) -> bytes:
    """Run download pdf bytes."""
    response = request_with_instrumentation(
        "GET",
        url,
        logger=logger,
        timeout=60,
        system="protective-security",
        operation="download_pspf_pdf",
        request_callable=requests.get,
    )
    response.raise_for_status()
    return response.content


def _extract_full_text(pdf_bytes: bytes) -> str:
    """Run extract full text."""
    if _PdfReader is None:
        raise RuntimeError(
            "pypdf is required for PSPF PDF parsing. Install with: pip install pypdf"
        )

    reader = _PdfReader(io.BytesIO(pdf_bytes))
    pages = [_clean_page_text(page.extract_text() or "") for page in reader.pages]
    return "\n".join(page for page in pages if page)


def _heading_level(number: str) -> int:
    """Run heading level."""
    return number.count(".")


def _format_heading(number: str, title: str) -> str:
    """Run format heading."""
    return f"{number} {title}".strip()


def _build_source_section(
    domain_code: str, headings: dict[int, tuple[str, str]], requirement_number: str
) -> str:
    """Run build source section."""
    parts = [domain_code]
    for level in sorted(headings):
        number, title = headings[level]
        parts.append(_format_heading(number, title))
    parts.append(f"Requirement {int(requirement_number)}")
    return " > ".join(parts)


def _current_control_family(domain_code: str, headings: dict[int, tuple[str, str]]) -> str:
    """Run current control family."""
    if headings:
        _, title = headings[max(headings)]
        return title
    return _DOMAIN_NAMES.get(domain_code, domain_code)


def _should_ignore_heading(number: str, title: str) -> bool:
    """Run should ignore heading."""
    if title.startswith(("Including ", "This includes ")):
        return True
    if number.isdigit() and int(number) > 30:
        return True
    return False


def _should_skip_context_line(line: str) -> bool:
    """Run should skip context line."""
    if line.startswith("Table ") or line.startswith("Figure "):
        return True
    if line.startswith("Req Number") or line.startswith("Status PSPF Reporting"):
        return True
    if line.startswith("PSPF Recommended Approach"):
        return True
    if line.startswith("Domain Section Applicability"):
        return True
    if line.startswith("Related Standards") or line.startswith("Related Guidance"):
        return True
    return False


def _should_stop_requirement_capture(line: str) -> bool:
    """Run should stop requirement capture."""
    if _REQ_HEADER_RE.match(line) or _HEADING_RE.match(line):
        return True
    if line.startswith("Table "):
        return True
    if line.startswith("Related Standards") or line.startswith("Related Guidance"):
        return True
    return False


def _pspf_keywords(
    domain_code: str,
    applicability: str,
    headings: dict[int, tuple[str, str]],
    requirement_text: str,
) -> list[str]:
    """Run pspf keywords."""
    keywords: list[str] = []
    keywords.extend(_DOMAIN_KEYWORDS.get(domain_code, []))

    applicability_lower = applicability.strip().lower()
    for needle, values in _APPLICABILITY_KEYWORDS.items():
        if needle in applicability_lower:
            keywords.extend(values)

    heading_titles = [title for _, title in headings.values()]
    for title in heading_titles:
        tuned = _CONTROL_KEYWORDS.get(title.strip().lower())
        if tuned:
            keywords.extend(tuned)

    keywords.extend(
        keywordise_values(
            domain_code,
            _DOMAIN_NAMES.get(domain_code, domain_code),
            applicability,
            *heading_titles,
            requirement_text[:200],
        )
    )
    return keywordise_values(*keywords)


def _parse_pspf_release_text(full_text: str) -> list[RequirementRecord]:
    """Run parse pspf release text."""
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    headings: dict[int, tuple[str, str]] = {}
    guidance_lines: list[str] = []
    records: list[RequirementRecord] = []
    in_table = False
    index = 0

    while index < len(lines):
        line = lines[index]

        header_match = _REQ_HEADER_RE.match(line)
        heading_match = _HEADING_RE.match(line)

        if header_match:
            in_table = False
            requirement_number = header_match.group("req")
            domain_code = header_match.group("domain")
            applicability = header_match.group("applicability")
            start_date = header_match.group("start_date")

            index += 1
            requirement_lines: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                if _should_stop_requirement_capture(candidate):
                    break
                requirement_lines.append(candidate)
                index += 1

            requirement_text = _flatten(" ".join(requirement_lines))
            guidance_text = _flatten(" ".join(guidance_lines))
            control_family = _current_control_family(domain_code, headings)
            source_section = _build_source_section(domain_code, headings, requirement_number)
            keywords = _pspf_keywords(
                domain_code,
                applicability,
                headings,
                requirement_text,
            )

            records.append(
                RequirementRecord(
                    requirement_id=f"PSPF-{requirement_number}",
                    framework=FRAMEWORK,
                    framework_version=FRAMEWORK_VERSION,
                    control_family=control_family,
                    maturity_level=None,
                    requirement_text=requirement_text,
                    guidance_text=guidance_text,
                    keywords=keywords,
                    source_uri=SOURCE_URI,
                    source_section=source_section,
                    effective_date=start_date,
                    jurisdiction_or_scope=JURISDICTION,
                )
            )
            continue

        if heading_match:
            in_table = False
            number = heading_match.group("number")
            title = heading_match.group("title")
            if _should_ignore_heading(number, title):
                index += 1
                continue
            level = _heading_level(number)
            headings[level] = (number, title)
            for existing in list(headings):
                if existing > level:
                    del headings[existing]
            guidance_lines = []
            index += 1
            continue

        if line.startswith("Table "):
            in_table = True
            index += 1
            continue

        if in_table:
            index += 1
            continue

        if not _should_skip_context_line(line):
            guidance_lines.append(line)
        index += 1

    return records


class PspfParser(BaseParser):
    """PspfParser."""

    def __init__(self, release_pdf_url: str = SOURCE_URI, **_kwargs: Any) -> None:
        """Run init."""
        self._release_pdf_url = release_pdf_url

    def parse(self) -> List[RequirementRecord]:
        """Run parse."""
        pdf_bytes = _download_pdf_bytes(self._release_pdf_url)
        full_text = _extract_full_text(pdf_bytes)
        records = _parse_pspf_release_text(full_text)
        logger.info("PSPF: parsed %d requirements", len(records))
        return records
