"""Corpus A framework constants and upload helpers.

Corpus A contains structured controls from recognised security frameworks
(ISM, Essential Eight, NIST CSF, etc.).  This module centralises the
framework key → display name mapping and all upload validation/classification
logic so it can be imported by both app.py and corpus endpoint modules
without circular dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import UploadFile

# ---------------------------------------------------------------------------
# Framework registry
# ---------------------------------------------------------------------------

_CORPUS_A_FRAMEWORKS: dict[str, str] = {
    "aescsf": "AESCSF",
    "cis_controls": "CIS Controls",
    "essential_eight": "Essential Eight",
    "ism": "ISM",
    "nist_ai_rmf": "NIST AI RMF",
    "nist_csf": "NIST CSF",
    "pci_dss": "PCI DSS",
    "pspf": "PSPF",
}

# Frameworks that require structured reference source files to be uploaded
# alongside the parsed-controls JSONL before ingestion can run.
_CORPUS_A_SOURCE_UPLOAD_REQUIRED_FRAMEWORKS: set[str] = {
    "cis_controls",
    "pci_dss",
}

# Maps framework key → file-extension → canonical target blob name.
_CORPUS_A_REFERENCE_UPLOAD_TARGETS: dict[str, dict[str, str]] = {
    "cis_controls": {
        ".xlsx": "CIS_Controls_Version_8.xlsx",
        ".pdf": "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf",
    },
    "pci_dss": {
        ".pdf": "PCI-DSS-v4_0_1.pdf",
    },
}


# ---------------------------------------------------------------------------
# Framework key normalisation
# ---------------------------------------------------------------------------


def _normalise_corpus_a_framework_key(raw: str) -> str | None:
    """Return a canonical framework key for *raw*, or None if unrecognised.

    Args:
        raw: The raw framework name or key to normalise.
    Returns:
        The canonical framework key, or None if unrecognised.
    """
    key = (raw or "").strip().lower()
    if not key:
        return None
    if key in _CORPUS_A_FRAMEWORKS:
        return key

    if key in {"nist", "nist csf", "csf", "csf 2.0"}:
        return "nist_csf"
    if key in {"nist ai rmf", "ai rmf", "nist_ai_rmf", "airmf"}:
        return "nist_ai_rmf"
    if key in {"essential eight", "e8"}:
        return "essential_eight"
    if key in {"aescsf", "aemo"}:
        return "aescsf"
    if key in {"cis", "cis controls", "cis_controls"}:
        return "cis_controls"
    if key in {"ism", "information security manual"}:
        return "ism"
    if key in {"pci", "pci dss", "pci-dss", "pci_dss", "pci dss v4"}:
        return "pci_dss"
    if key in {"pspf", "protective security policy framework"}:
        return "pspf"
    if key == "all":
        return "all"
    return None


def _selected_corpus_a_frameworks(frameworks: list[str] | None) -> list[str]:
    """Return the sorted list of canonical framework keys selected by *frameworks*.

    If *frameworks* is empty or None, all known frameworks are returned.

    Args:
        frameworks: Optional list of raw framework names or keys to normalise and select.

    Returns:
        A sorted list of canonical framework keys corresponding to the selected frameworks.
    """
    if not frameworks:
        return sorted(_CORPUS_A_FRAMEWORKS.keys())

    selected: list[str] = []
    for raw in frameworks:
        key = _normalise_corpus_a_framework_key(raw)
        if key == "all":
            return sorted(_CORPUS_A_FRAMEWORKS.keys())
        if key and key not in selected:
            selected.append(key)

    return selected if selected else sorted(_CORPUS_A_FRAMEWORKS.keys())


# ---------------------------------------------------------------------------
# Reference upload helpers
# ---------------------------------------------------------------------------


def _prepare_corpus_a_reference_uploads(
    framework: str,
    files: list["UploadFile"],
) -> tuple[str, list[tuple["UploadFile", str, str]]]:
    """Validate and map *files* to their canonical target blob names for *framework*.

    Returns ``(framework_key, [(upload_file, original_name, target_name), ...])``.
    Raises :class:`ValueError` on validation errors.

    Args:
        framework: The raw framework name or key to normalise.
        files: The list of uploaded files to validate and map.
    Returns:
        A tuple containing the canonical framework key and a list of tuples for each file, where each tuple contains the UploadFile object, the original filename, and the canonical target blob name.
    Raises:
        ValueError: If the framework is unrecognised, unsupported, or if the uploaded files do not match the expected types or counts for the framework.
    """
    from fastapi import UploadFile  # noqa: F401 — imported for type narrowing at runtime

    key = _normalise_corpus_a_framework_key(framework)
    if not key or key not in _CORPUS_A_REFERENCE_UPLOAD_TARGETS:
        raise ValueError(
            "Corpus A reference uploads are only supported for CIS Controls and PCI DSS."
        )

    target_map = _CORPUS_A_REFERENCE_UPLOAD_TARGETS[key]
    selected_by_target: dict[str, tuple["UploadFile", str]] = {}

    for file in files:
        original_name = file.filename or "uploaded.bin"
        ext = Path(original_name).suffix.lower()
        target_name = target_map.get(ext)
        if not target_name:
            allowed = ", ".join(sorted(target_map.keys()))
            raise ValueError(
                f"Unsupported file '{original_name}' for {_CORPUS_A_FRAMEWORKS[key]}; "
                f"expected file types: {allowed}."
            )
        if target_name in selected_by_target:
            raise ValueError(
                f"Received multiple files for {_CORPUS_A_FRAMEWORKS[key]} source type '{ext}'."
            )
        selected_by_target[target_name] = (file, original_name)

    missing_targets = [name for name in target_map.values() if name not in selected_by_target]
    if missing_targets:
        raise ValueError(
            "Missing required source files for "
            f"{_CORPUS_A_FRAMEWORKS[key]}: {', '.join(missing_targets)}."
        )

    prepared = [
        (upload_file, original_name, target_name)
        for target_name, (upload_file, original_name) in selected_by_target.items()
    ]
    return key, prepared


def _classify_corpus_a_auto_uploads(
    files: list["UploadFile"],
) -> dict[str, list["UploadFile"]]:
    """Classify uploaded Corpus A source files into CIS/PCI framework buckets.

    Raises :class:`ValueError` if a file cannot be classified or is unsupported.

    Args:
        files: The list of uploaded files to classify.
    Returns:
        A dictionary mapping framework keys to lists of UploadFile objects for each classified framework.
    Raises:
        ValueError: If a file cannot be classified into a framework or if unsupported file types are provided.
    """
    grouped: dict[str, list["UploadFile"]] = {
        "cis_controls": [],
        "pci_dss": [],
    }
    ambiguous_pdfs: list["UploadFile"] = []

    for file in files:
        original_name = (file.filename or "uploaded.bin").strip()
        lower_name = original_name.lower()
        ext = Path(original_name).suffix.lower()

        if ext == ".xlsx":
            grouped["cis_controls"].append(file)
            continue
        if ext != ".pdf":
            raise ValueError(
                f"Unsupported file '{original_name}' for auto mode; expected .pdf or .xlsx."
            )

        if "pci" in lower_name and "dss" in lower_name:
            grouped["pci_dss"].append(file)
        elif "cis" in lower_name and "control" in lower_name:
            grouped["cis_controls"].append(file)
        else:
            ambiguous_pdfs.append(file)

    cis_has_xlsx = any(
        Path((item.filename or "").strip()).suffix.lower() == ".xlsx"
        for item in grouped["cis_controls"]
    )
    cis_pdf_count = sum(
        1
        for item in grouped["cis_controls"]
        if Path((item.filename or "").strip()).suffix.lower() == ".pdf"
    )
    pci_pdf_count = sum(
        1
        for item in grouped["pci_dss"]
        if Path((item.filename or "").strip()).suffix.lower() == ".pdf"
    )

    for file in ambiguous_pdfs:
        if cis_has_xlsx and cis_pdf_count == 0:
            grouped["cis_controls"].append(file)
            cis_pdf_count += 1
            continue
        if pci_pdf_count == 0:
            grouped["pci_dss"].append(file)
            pci_pdf_count += 1
            continue
        raise ValueError(
            "Could not auto-map one or more PDF files. "
            "Choose a specific framework, or use canonical filenames for CIS/PCI sources."
        )

    selected = {framework: items for framework, items in grouped.items() if items}
    if not selected:
        raise ValueError("No supported Corpus A source files were provided.")
    return selected
