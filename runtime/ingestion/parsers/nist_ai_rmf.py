"""Parser for the NIST AI Risk Management Framework (AI RMF 1.0).

The NIST AI RMF provides guidance for managing risks associated with artificial
intelligence systems. It complements the NIST Cybersecurity Framework (CSF) 2.0
and provides AI-specific risk management guidance.

Source: NIST AI 100-1, January 2024
https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf

Additional Resources: https://airc.nist.gov/airmf-resources/

The parser extracts control requirements from the framework and emits one
RequirementRecord per control objective.

Licensing: NIST AI RMF documentation is public domain. Derived artefacts
should maintain appropriate attribution to NIST.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any, List

import requests  # type: ignore[import-untyped]

try:
    from runtime.outbound_instrumentation import request_with_instrumentation
except ImportError:  # Container layout copies modules to /app, not /app/runtime.
    from outbound_instrumentation import request_with_instrumentation

from .base import BaseParser, RequirementRecord, filter_keywords, keywordise_values

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfReader = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAMEWORK = "NIST AI RMF"
FRAMEWORK_VERSION = "1.0"
EFFECTIVE_DATE = "January 2024"
JURISDICTION = "United States"
SOURCE_URI = "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf"

_DEFAULT_PDF_PATH = Path(__file__).resolve().parents[2] / "samples" / "NIST.AI.100-1.pdf"

_PLAYBOOK_BASE_URI = "https://airc.nist.gov/airmf-resources/playbook"
_PLAYBOOK_SLUG_BY_TOKEN = {
    "GOVERN": "govern",
    "MAP": "map",
    "MEASURE": "measure",
    "MANAGE": "manage",
}

_FUNCTION_NAME_BY_TOKEN = {
    "GOVERN": "Govern",
    "MAP": "Map",
    "MEASURE": "Measure",
    "MANAGE": "Manage",
}

_CONTROL_ENTRY_RE = re.compile(
    r"\b(GOVERN|MAP|MEASURE|MANAGE)\s+(\d+(?:\.\d+)?)\s*:\s*(.+?)(?=\b(?:GOVERN|MAP|MEASURE|MANAGE)\s+\d+(?:\.\d+)?\s*:|Categories\s+Subcategories|Page\s+\d+\s+NIST|\Z)",
    re.IGNORECASE | re.DOTALL,
)


class NistAiRmfParser(BaseParser):
    """Parser for NIST AI RMF 1.0 PDF document.

    Reads the official NIST AI 100-1 PDF and extracts control objectives,
    mapped to a structure compatible with existing RequirementRecord output format.

    Constructor Parameters
    ----------------------
    pdf_path : str | Path, optional
        Optional local path override to NIST.AI.100-1.pdf. If omitted, the
        parser tries ``runtime/samples/NIST.AI.100-1.pdf`` first and then
        downloads from the official NIST source URL.

    Attributes
    ----------
    pdf_path : Path
        Resolved path to the PDF document.

    Examples
    --------
    >>> parser = NistAiRmfParser()
    >>> records = parser.parse()
    >>> print(f"Extracted {len(records)} control objectives")
    """

    def __init__(self, pdf_path: str | Path | None = None, *, fetch_guidance: bool = True) -> None:
        """Initialise the NIST AI RMF parser.

        Parameters
        ----------
        pdf_path : str | Path, optional
            Optional local path override to NIST.AI.100-1.pdf.
        fetch_guidance : bool, optional
            When True, guidance text is sourced from NIST AI RMF Playbook
            per-requirement pages; when False, parser falls back to PDF category text.
        """
        self.pdf_path = Path(pdf_path).resolve() if pdf_path is not None else None
        self.fetch_guidance = bool(fetch_guidance)

    def parse(self) -> List[RequirementRecord]:
        """Parse NIST AI RMF 1.0 PDF and return control objectives as RequirementRecords.

        Returns
        -------
        list[RequirementRecord]
            List of extracted control objectives, one per function and objective group.

        Raises
        ------
        ImportError
            If pypdf not installed.
        """
        if _PdfReader is None:
            raise ImportError(
                "pypdf is required to parse NIST AI RMF PDF. " "Install with: pip install pypdf"
            )

        records: List[RequirementRecord] = []

        try:
            reader = self._load_pdf_reader()
            text = self._extract_text(reader)

            # Parse text to extract controls
            # The PDF structure contains control objectives organised by function
            records.extend(self._parse_controls_from_text(text))

        except Exception as e:
            logger.error("Failed to parse NIST AI RMF PDF: %s", e)
            raise

        logger.info("Parsed %d control objectives from NIST AI RMF", len(records))
        return records

    def _load_pdf_reader(self) -> Any:
        """Load PDF reader from local path override or official NIST source URI.

        Resolution order:
        1) Explicit parser ``pdf_path`` if provided and exists.
        2) Default local sample path if present.
        3) Download from official ``SOURCE_URI``.
        """
        if _PdfReader is None:
            raise ImportError(
                "pypdf is required to parse NIST AI RMF PDF. " "Install with: pip install pypdf"
            )

        candidate_paths: list[Path] = []
        if self.pdf_path is not None:
            candidate_paths.append(self.pdf_path)
        candidate_paths.append(_DEFAULT_PDF_PATH)

        for path in candidate_paths:
            if path.exists():
                logger.info("NIST AI RMF: loading PDF from local path %s", path)
                return _PdfReader(str(path))

        logger.info("NIST AI RMF: downloading source PDF from %s", SOURCE_URI)
        response = request_with_instrumentation(
            "GET",
            SOURCE_URI,
            logger=logger,
            timeout=90,
            system="nist",
            operation="download_nist_ai_rmf_pdf",
            request_callable=requests.get,
        )
        response.raise_for_status()
        return _PdfReader(io.BytesIO(response.content))

    def _extract_text(self, reader: Any) -> str:
        """Extract plain text from PDF.

        Parameters
        ----------
        reader : pypdf.PdfReader
            Initialised PDF reader.

        Returns
        -------
        str
            Concatenated text from all pages.
        """
        text_parts = []
        for page_num, page in enumerate(reader.pages):
            try:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
            except Exception as e:
                logger.warning(
                    "Failed to extract text from page %d of NIST AI RMF: %s",
                    page_num,
                    e,
                )
        return "\n".join(text_parts)

    def _parse_controls_from_text(self, text: str) -> List[RequirementRecord]:
        """Parse control objectives from extracted text.

        Parameters
        ----------
        text : str
            Full text extracted from PDF.

        Returns
        -------
        list[RequirementRecord]
            Parsed control objectives.
        """
        normalised = _normalise_pdf_text(text)
        entries = _extract_control_entries(normalised)

        if not entries:
            raise RuntimeError(
                "No NIST AI RMF controls found in source text. "
                "Expected GOVERN/MAP/MEASURE/MANAGE numbered controls."
            )

        category_guidance: dict[tuple[str, str], str] = {}
        for function_token, control_code, statement in entries:
            if "." in control_code:
                continue
            category_guidance[(function_token, control_code)] = statement

        playbook_guidance = _build_playbook_guidance_map(fetch_guidance=self.fetch_guidance)

        records: List[RequirementRecord] = []
        for function_token, control_code, statement in entries:
            if "." not in control_code:
                # Category lines (e.g. GOVERN 1:) are used as guidance context.
                continue

            function_name = _FUNCTION_NAME_BY_TOKEN.get(function_token, function_token.title())
            major = control_code.split(".", maxsplit=1)[0]
            requirement_id = f"NIST-AI-RMF-{function_token}-{control_code.replace('.', '-')}"
            playbook_key = f"{function_token} {control_code}"
            guidance = category_guidance.get((function_token, major), "")
            guidance_uri = SOURCE_URI
            playbook_entry = playbook_guidance.get(playbook_key)
            if playbook_entry:
                guidance = playbook_entry[0]
                guidance_uri = playbook_entry[1]

            control_family = _build_control_family(function_name, major, guidance)

            records.append(
                RequirementRecord(
                    requirement_id=requirement_id,
                    framework=FRAMEWORK,
                    framework_version=FRAMEWORK_VERSION,
                    control_family=control_family,
                    maturity_level=None,
                    requirement_text=statement,
                    guidance_text=guidance,
                    keywords=filter_keywords(
                        keywordise_values(function_name, control_code, statement, guidance)
                    ),
                    source_uri=guidance_uri,
                    source_section=f"{function_name} {control_code}",
                    effective_date=EFFECTIVE_DATE,
                    jurisdiction_or_scope=JURISDICTION,
                )
            )

        return records


def _normalise_pdf_text(text: str) -> str:
    """Normalise PDF-extracted text to improve control-pattern matching."""
    # Join line-wrap hyphenation artifacts: "man-\nagement" -> "management"
    cleaned = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    # Collapse line breaks and multiple spaces for easier regex scanning.
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _extract_control_entries(text: str) -> list[tuple[str, str, str]]:
    """Extract numbered AI RMF entries as ``(function, code, statement)`` tuples."""
    entries: list[tuple[str, str, str]] = []
    for match in _CONTROL_ENTRY_RE.finditer(text):
        function_token = match.group(1).upper()
        control_code = match.group(2).strip()
        statement = _clean_statement(match.group(3))
        if not statement:
            continue
        entries.append((function_token, control_code, statement))
    return entries


def _build_playbook_guidance_map(fetch_guidance: bool = True) -> dict[str, tuple[str, str]]:
    """Build ``FUNCTION CODE -> (guidance, bookmark_url)`` map from AI RMF playbook pages."""
    if not fetch_guidance:
        return {}

    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        logger.warning("bs4 not installed; using PDF category text for NIST AI RMF guidance")
        return {}

    guidance_map: dict[str, tuple[str, str]] = {}
    for function_token, slug in _PLAYBOOK_SLUG_BY_TOKEN.items():
        page_url = f"{_PLAYBOOK_BASE_URI}/{slug}/"
        try:
            response = request_with_instrumentation(
                "GET",
                page_url,
                logger=logger,
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
                system="nist",
                operation="fetch_nist_ai_rmf_playbook",
                request_callable=requests.get,
            )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning(
                "Failed to fetch NIST AI RMF playbook page %s: %s",
                page_url,
                exc,
            )
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        guidance_map.update(_extract_playbook_page_guidance(soup, function_token, page_url))

    return guidance_map


def _extract_playbook_page_guidance(
    soup: Any, function_token: str, page_url: str
) -> dict[str, tuple[str, str]]:
    """Extract subcontrol guidance from one playbook HTML page."""
    extracted: dict[str, tuple[str, str]] = {}

    heading_re = re.compile(
        rf"^{re.escape(function_token)}\s+(\d+\.\d+)\s*$",
        re.IGNORECASE,
    )

    for heading in soup.find_all(["h3", "h4"]):
        heading_text = heading.get_text(" ", strip=True)
        match = heading_re.match(heading_text)
        if not match:
            continue

        control_code = match.group(1)
        key = f"{function_token} {control_code}"
        bookmark_url = f"{page_url}#{function_token.lower()}-{control_code.replace('.', '-')}"

        parts: list[str] = []
        sibling = heading.find_next_sibling()
        while sibling is not None and getattr(sibling, "name", "") not in {"h2", "h3", "h4"}:
            name = getattr(sibling, "name", "")
            if name == "p":
                text = _clean_statement(sibling.get_text(" ", strip=True))
                if text:
                    parts.append(text)
            elif name in {"ul", "ol"}:
                for li in sibling.find_all("li", recursive=False):
                    text = _clean_statement(li.get_text(" ", strip=True))
                    if text:
                        parts.append(text)
            sibling = sibling.find_next_sibling()

        guidance = _clean_statement(" ".join(parts))
        if guidance:
            extracted[key] = (guidance, bookmark_url)

    return extracted


def _clean_statement(text: str) -> str:
    """Clean parser statement text while preserving normative content."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.strip(" :-")
    return cleaned


def _build_control_family(function_name: str, major: str, guidance: str) -> str:
    """Build a readable control family label from function, category, and guidance."""
    base = f"{function_name} {major}"
    guidance_clean = _clean_statement(guidance)
    if not guidance_clean:
        return base

    # Use first sentence as short category label, capped to keep UI lists readable.
    first_sentence = re.split(r"(?<=[.!?])\s+", guidance_clean, maxsplit=1)[0]
    label = first_sentence.rstrip(".")
    if len(label) > 96:
        label = label[:96].rstrip() + "..."
    return f"{base} - {label}"


def _slugify(text: str) -> str:
    """Convert *text* to a lowercase hyphen-delimited slug.

    Parameters
    ----------
    text : str
        Text to slugify.

    Returns
    -------
    str
        Slugified text.

    Examples
    --------
    >>> _slugify("Risk Assessment and Mitigation")
    'risk-assessment-and-mitigation'
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
