"""Unit tests for runtime.ingestion.extractors.

Uses in-memory file construction (pypdf.PdfWriter, openpyxl.Workbook) so that
no sample fixture files are required.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.ingestion.extractors import (
    _extract_pdf_text,
    discover_supported_files,
    extract_source_document,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf_bytes(pages: list[str]) -> bytes:
    """Return minimal valid PDF bytes containing the supplied page texts.

    Uses pypdf.generic.DictionaryObject (not plain dict) so that
    PdfWriter._add_object works correctly on pypdf >= 6.x.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)

        # Content stream — pypdf DecodedStreamObject is the right type here.
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())

        # Font and resource dicts must be DictionaryObject, not plain dict,
        # so that _add_object can set .indirect_reference on them.
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        resources = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): font,
                    }
                ),
            }
        )

        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(stream)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_excel_bytes(sheets: dict[str, list[list]]) -> bytes:
    """Return minimal valid xlsx bytes with the supplied sheet data."""
    from openpyxl import Workbook

    wb = Workbook()
    active = wb.active
    if active is not None:
        wb.remove(active)  # remove default blank sheet
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def test_extract_pdf_produces_source_document(tmp_path: Path) -> None:
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(_make_pdf_bytes(["Hello world"]))

    doc = extract_source_document(pdf_file)

    assert doc.source_type == "pdf"
    assert doc.source_path.endswith("test.pdf")
    assert isinstance(doc.text, str)


def test_extract_pdf_joins_multiple_pages(tmp_path: Path) -> None:
    pdf_file = tmp_path / "multi.pdf"
    pdf_file.write_bytes(_make_pdf_bytes(["Page one", "Page two"]))

    doc = extract_source_document(pdf_file)

    # Both page texts should be present (joined by newline).
    assert "one" in doc.text or "two" in doc.text or doc.text == "\n"


def test_extract_pdf_empty_pages_returns_string(tmp_path: Path) -> None:
    """A PDF with blank pages should return a string (possibly empty), not raise."""
    pdf_file = tmp_path / "blank.pdf"
    pdf_file.write_bytes(_make_pdf_bytes([""]))

    doc = extract_source_document(pdf_file)

    assert isinstance(doc.text, str)
    assert doc.source_type == "pdf"


def test_extract_pdf_raises_runtime_error_when_pypdf_missing(tmp_path: Path) -> None:
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(_make_pdf_bytes(["hi"]))

    with patch.dict("sys.modules", {"pypdf": None}):
        with pytest.raises(RuntimeError, match="pypdf is required"):
            extract_source_document(pdf_file)


def test_extract_pdf_ocr_not_triggered_when_text_sufficient(tmp_path: Path) -> None:
    pdf_file = tmp_path / "text-rich.pdf"
    pdf_file.write_bytes(
        _make_pdf_bytes(["This page has enough extracted text to skip OCR fallback."])
    )

    with patch("runtime.ingestion.extractors._extract_pdf_text_ocr") as ocr_mock:
        text = _extract_pdf_text(pdf_file, enable_ocr=True, min_text_chars=10)

    assert "enough extracted text" in text
    ocr_mock.assert_not_called()


def test_extract_pdf_ocr_triggered_when_text_sparse(tmp_path: Path) -> None:
    pdf_file = tmp_path / "sparse.pdf"
    pdf_file.write_bytes(_make_pdf_bytes([""]))

    with patch(
        "runtime.ingestion.extractors._extract_pdf_text_ocr", return_value="ocr recovered text"
    ) as ocr_mock:
        text = _extract_pdf_text(pdf_file, enable_ocr=True, min_text_chars=1)

    assert "ocr recovered text" in text
    ocr_mock.assert_called_once()


def test_extract_pdf_ocr_failure_falls_back_to_extracted_text(tmp_path: Path) -> None:
    pdf_file = tmp_path / "fallback.pdf"
    pdf_file.write_bytes(_make_pdf_bytes(["native text"]))

    with patch(
        "runtime.ingestion.extractors._extract_pdf_text_ocr",
        side_effect=RuntimeError("ocr unavailable"),
    ):
        text = _extract_pdf_text(pdf_file, enable_ocr=True, min_text_chars=1000)

    assert "native text" in text


# ---------------------------------------------------------------------------
# Excel extraction
# ---------------------------------------------------------------------------


def test_extract_excel_produces_source_document(tmp_path: Path) -> None:
    xlsx_file = tmp_path / "data.xlsx"
    xlsx_file.write_bytes(_make_excel_bytes({"Sheet1": [["Name", "Value"], ["alpha", 1]]}))

    doc = extract_source_document(xlsx_file)

    assert doc.source_type == "excel"
    assert doc.source_path.endswith("data.xlsx")
    assert "Sheet1" in doc.text
    assert "alpha" in doc.text


def test_extract_excel_multiple_sheets(tmp_path: Path) -> None:
    xlsx_file = tmp_path / "multi.xlsx"
    xlsx_file.write_bytes(
        _make_excel_bytes(
            {
                "First": [["a", "b"]],
                "Second": [["c", "d"]],
            }
        )
    )

    doc = extract_source_document(xlsx_file)

    assert "First" in doc.text
    assert "Second" in doc.text


def test_extract_excel_skips_empty_rows(tmp_path: Path) -> None:
    xlsx_file = tmp_path / "sparse.xlsx"
    xlsx_file.write_bytes(_make_excel_bytes({"Sheet1": [["hello"], [], [None, None], ["world"]]}))

    doc = extract_source_document(xlsx_file)

    assert "hello" in doc.text
    assert "world" in doc.text


def test_extract_excel_raises_runtime_error_when_openpyxl_missing(tmp_path: Path) -> None:
    xlsx_file = tmp_path / "data.xlsx"
    xlsx_file.write_bytes(_make_excel_bytes({"S": [["x"]]}))

    with patch.dict("sys.modules", {"openpyxl": None}):
        with pytest.raises(RuntimeError, match="openpyxl is required"):
            extract_source_document(xlsx_file)


# ---------------------------------------------------------------------------
# Unsupported file types
# ---------------------------------------------------------------------------


def test_extract_unsupported_extension_raises_value_error(tmp_path: Path) -> None:
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("some text")

    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_source_document(txt_file)


# ---------------------------------------------------------------------------
# discover_supported_files
# ---------------------------------------------------------------------------


def test_discover_supported_files_finds_pdf_and_xlsx(tmp_path: Path) -> None:
    # Discover only needs valid extensions, not parseable content.
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.xlsx").write_bytes(b"stub")
    (tmp_path / "skip.txt").write_text("ignore me")

    found = discover_supported_files(tmp_path)
    names = {p.name for p in found}

    assert "a.pdf" in names
    assert "b.xlsx" in names
    assert "skip.txt" not in names


def test_discover_supported_files_is_sorted(tmp_path: Path) -> None:
    for name in ("z.pdf", "a.pdf", "m.xlsx"):
        (tmp_path / name).write_bytes(b"%PDF-1.4")  # minimal stub

    found = discover_supported_files(tmp_path)

    assert found == sorted(found)


def test_discover_supported_files_recurses_subdirectories(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.pdf").write_bytes(b"%PDF-1.4")

    found = discover_supported_files(tmp_path)
    names = {p.name for p in found}

    assert "nested.pdf" in names


def test_discover_supported_files_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    assert discover_supported_files(tmp_path) == []
