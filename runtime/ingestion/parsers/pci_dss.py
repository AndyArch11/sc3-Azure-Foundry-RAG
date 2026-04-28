"""Parser for PCI DSS v4.0.1 using a local sample PDF.

The PDF provides both the normative requirement text and associated guidance
(Purpose, Good Practice, Definitions, Customized Approach Objective sections).

Source file must be downloaded by the operator from:
    https://docs-prv.pcisecuritystandards.org/PCI%20DSS/Standard/PCI-DSS-v4_0_1.pdf

Licensing: PCI DSS is © 2006-2024 PCI Security Standards Council, LLC. The
operator is responsible for complying with any terms associated with downloading
and using the document. Derived artefacts should not be redistributed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, List

from .base import BaseParser, RequirementRecord, keywordise_values

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfReader = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAMEWORK = "PCI DSS"
FRAMEWORK_VERSION = "v4.0.1"
EFFECTIVE_DATE = "June 2024"
JURISDICTION = "Global"
SOURCE_URI = "https://docs-prv.pcisecuritystandards.org/PCI%20DSS/Standard/PCI-DSS-v4_0_1.pdf"

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"
_DEFAULT_PDF_PATH = _SAMPLES_ROOT / "api" / "corpus-a" / "PCI-DSS-v4_0_1.pdf"

# Top-level requirement number → (domain family, requirement title)
_REQUIREMENT_FAMILIES: dict[str, tuple[str, str]] = {
    "1": (
        "Build and Maintain a Secure Network and Systems",
        "Install and Maintain Network Security Controls",
    ),
    "2": (
        "Build and Maintain a Secure Network and Systems",
        "Apply Secure Configurations to All System Components",
    ),
    "3": (
        "Protect Account Data",
        "Protect Stored Account Data",
    ),
    "4": (
        "Protect Account Data",
        "Protect Cardholder Data with Strong Cryptography During Transmission Over Open, Public Networks",
    ),
    "5": (
        "Maintain a Vulnerability Management Program",
        "Protect All Systems and Networks from Malicious Software",
    ),
    "6": (
        "Maintain a Vulnerability Management Program",
        "Develop and Maintain Secure Systems and Software",
    ),
    "7": (
        "Implement Strong Access Control Measures",
        "Restrict Access to System Components and Cardholder Data by Business Need to Know",
    ),
    "8": (
        "Implement Strong Access Control Measures",
        "Identify Users and Authenticate Access to System Components",
    ),
    "9": (
        "Implement Strong Access Control Measures",
        "Restrict Physical Access to Cardholder Data",
    ),
    "10": (
        "Regularly Monitor and Test Networks",
        "Log and Monitor All Access to System Components and Cardholder Data",
    ),
    "11": (
        "Regularly Monitor and Test Networks",
        "Test Security of Systems and Networks Regularly",
    ),
    "12": (
        "Maintain an Information Security Policy",
        "Support Information Security with Organizational Policies and Programs",
    ),
}

# Verbs that identify a testing procedure line rather than a normative requirement.
_TESTING_VERB_RE = re.compile(
    r"^(examine|interview|review|verify|assess|perform|identify|observe|confirm|ask|check)\b",
    re.IGNORECASE,
)

# Noise labels from the guidance column that should not produce requirement text.
_GUIDANCE_LABEL_RE = re.compile(
    r"^(Defined Approach Requirements|Defined Approach Testing Procedures|Purpose|"
    r"Customized Approach Objective|Good Practice|Definitions|Examples|"
    r"Applicability Notes|Requirements and Testing Procedures Guidance|"
    r"Note\b)",
    re.IGNORECASE,
)

# Running page header/footer present on every requirements page.
_PAGE_HEADER_RE = re.compile(
    r"Payment Card Industry Data Security Standard:.*?All Rights Reserved\. Page \S+\s*\n",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten(text: str) -> str:
    """Collapse whitespace and newlines to single spaces, trim the result."""
    text = text.replace("-\n", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_testing_procedure_tail(req_id: str, req_text_raw: str) -> str:
    """Remove testing-procedure tail that follows a requirement statement.

    In the PCI PDF, a requirement block often contains both:
    - normative requirement prose
    - testing procedure lines that repeat the same requirement ID

    Keep only the normative portion by truncating at the first repeated
    requirement marker that introduces testing steps.
    """
    # Example: "1.2.1 Examine ..." (same requirement ID followed by testing verb)
    inline_testing_re = re.compile(
        rf"\b{re.escape(req_id)}\s+(?:examine|interview|review|verify|assess|perform|identify|observe|confirm|ask|check)\b",
        re.IGNORECASE,
    )
    # Example: "1.2.1.a Examine ..." (lettered testing sub-step)
    lettered_step_re = re.compile(rf"\b{re.escape(req_id)}\.[a-z]\b", re.IGNORECASE)

    cut_points = []
    inline_match = inline_testing_re.search(req_text_raw)
    if inline_match:
        cut_points.append(inline_match.start())

    lettered_match = lettered_step_re.search(req_text_raw)
    if lettered_match:
        cut_points.append(lettered_match.start())

    if not cut_points:
        return req_text_raw

    return req_text_raw[: min(cut_points)]


def _extract_full_text(pdf_path: Path) -> str:
    """Extract and clean page text from the PDF, skipping preamble pages."""
    if _PdfReader is None:
        raise RuntimeError(
            "pypdf is required for PCI DSS PDF parsing. Install with: pip install pypdf"
        )
    reader = _PdfReader(str(pdf_path))
    parts: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").replace("\xa0", " ")
        text = _PAGE_HEADER_RE.sub("", text)
        parts.append(text)
    return "\n".join(parts)


def _build_section_title_map(full_text: str) -> dict[str, str]:
    """Map sub-section IDs (e.g. "1.2") to their title text."""
    result: dict[str, str] = {}
    for m in re.finditer(r"^(\d+\.\d+)\s{1,4}([A-Z].{10,}?)\.?\s*$", full_text, re.MULTILINE):
        key = m.group(1)
        if key not in result:
            result[key] = m.group(2).strip()
    return result


def _build_requirement_and_guidance_maps(
    full_text: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (requirement_texts, section_guidance).

    requirement_texts : "1.1.1" → normative requirement text
    section_guidance  : "1.1"   → consolidated section-level guidance text
    """
    # Split the document into x.y section blocks based on section headers.
    # Pattern matches lines like "1.2  Network security controls (NSCs) are configured…"
    section_split_re = re.compile(r"(?=^(\d+\.\d+)\s{1,4}[A-Z].{10,}?\.?\s*$)", re.MULTILINE)
    blocks = section_split_re.split(full_text)

    # blocks will be: [text, "1.1", text, "1.2", text, ...]  (split inserts group 1)
    # Pair them up: odd indices are the section IDs, even indices are the content.
    requirement_texts: dict[str, str] = {}
    section_guidance: dict[str, str] = {}

    i = 0
    while i < len(blocks):
        block = blocks[i]
        # Determine which section this block belongs to
        section_id_match = re.match(r"^(\d+\.\d+)$", block.strip())
        if section_id_match:
            section_id = section_id_match.group(1)
            content = blocks[i + 1] if i + 1 < len(blocks) else ""
            i += 2
        else:
            # Preamble content before the first section — skip
            i += 1
            continue

        # Within the section content, find all x.y.z blocks.
        # A sub-requirement line starts with exactly three dot-separated numbers
        # (not x.y.z.a which is a testing procedure sub-step).
        sub_req_re = re.compile(
            r"^(\d+\.\d+\.\d+)\s+(.+?)(?=^\d+\.\d+\.\d+\s|\Z)",
            re.MULTILINE | re.DOTALL,
        )

        guidance_parts: list[str] = []
        prev_end = 0

        for m in sub_req_re.finditer(content):
            req_id = m.group(1)
            req_text_raw = m.group(2)

            # Accumulate text between the previous match and this one as guidance
            interstitial = content[prev_end : m.start()]
            guidance_parts.append(interstitial)
            prev_end = m.end()

            # Skip testing procedure sub-steps (x.y.z.a, x.y.z.b …)
            # These appear as "1.1.1.a Examine …" in the raw text
            if re.match(r"^\d+\.\d+\.\d+\.[a-z]", req_id):
                continue

            req_text_clean = _flatten(_strip_testing_procedure_tail(req_id, req_text_raw))

            # Skip if this is a testing procedure (starts with an examination verb)
            if _TESTING_VERB_RE.match(req_text_clean):
                continue

            # Also skip continuation markers added by the PDF layout
            if re.match(r"^\(continued\)", req_text_clean, re.IGNORECASE):
                continue

            # Only record the FIRST seen text for each ID
            if req_id not in requirement_texts and len(req_text_clean) > 10:
                requirement_texts[req_id] = req_text_clean

        # Remaining text after the last sub-requirement
        guidance_parts.append(content[prev_end:])

        # Build section guidance: strip noise labels and flatten
        raw_guidance = " ".join(guidance_parts)
        lines = raw_guidance.splitlines()
        # Pattern matches the section header itself, e.g. "1.2 Network security controls…"
        section_header_re = re.compile(r"^\d+\.\d+\s{1,4}[A-Z].{10,}?\.?\s*$")
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if _GUIDANCE_LABEL_RE.match(stripped):
                continue
            if section_header_re.match(stripped):
                continue
            # Skip pure numeric/page-ref lines
            if re.match(r"^[\d\s\.\-–©]+$", stripped):
                continue
            cleaned_lines.append(stripped)

        guidance_text = _flatten(" ".join(cleaned_lines))
        if guidance_text:
            section_guidance[section_id] = guidance_text

    return requirement_texts, section_guidance


# ---------------------------------------------------------------------------
# Parser class
# ---------------------------------------------------------------------------


class PciDssParser(BaseParser):
    """PciDssParser."""

    def __init__(
        self,
        pdf_path: str | Path = _DEFAULT_PDF_PATH,
        **_kwargs: Any,
    ) -> None:
        """Run init."""
        self._pdf_path = Path(pdf_path)

    def parse(self) -> List[RequirementRecord]:
        """Run parse."""
        if not self._pdf_path.exists():
            raise RuntimeError(
                f"PCI DSS PDF not found: {self._pdf_path}\n"
                "Download from: https://docs-prv.pcisecuritystandards.org/PCI%20DSS/Standard/PCI-DSS-v4_0_1.pdf"
            )

        full_text = _extract_full_text(self._pdf_path)
        section_titles = _build_section_title_map(full_text)
        requirement_texts, section_guidance = _build_requirement_and_guidance_maps(full_text)

        records: list[RequirementRecord] = []
        counters: dict[str, int] = {}

        for req_id in sorted(requirement_texts, key=lambda x: [int(n) for n in x.split(".")]):
            parts = req_id.split(".")
            if len(parts) != 3:
                continue

            major = parts[0]
            section_key = f"{parts[0]}.{parts[1]}"

            family_info = _REQUIREMENT_FAMILIES.get(major)
            if family_info is None:
                logger.warning(
                    "Unknown top-level requirement number %s — skipping %s", major, req_id
                )
                continue

            _, req_title = family_info
            section_title = section_titles.get(section_key, "")

            guidance_text = section_guidance.get(section_key, "")
            req_text = requirement_texts[req_id]

            req_id_slug = req_id.replace(".", "_")
            record_id = f"PCIDSS-{req_id_slug}"

            keywords = keywordise_values(
                FRAMEWORK,
                FRAMEWORK_VERSION,
                req_title,
                section_title,
                req_text[:200],
            )

            records.append(
                RequirementRecord(
                    requirement_id=record_id,
                    framework=FRAMEWORK,
                    framework_version=FRAMEWORK_VERSION,
                    control_family=req_title,
                    maturity_level=None,
                    requirement_text=req_text,
                    guidance_text=guidance_text,
                    keywords=keywords,
                    source_uri=SOURCE_URI,
                    source_section=f"Requirement {major} > {section_key} > {req_id}",
                    effective_date=EFFECTIVE_DATE,
                    jurisdiction_or_scope=JURISDICTION,
                )
            )

        logger.info("PCI DSS: parsed %d requirements", len(records))
        return records
