from __future__ import annotations

from pathlib import Path

from .models import SourceDocument


SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xlsm", ".xltx", ".xltm"}


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF ingestion") from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _extract_excel_text(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required for Excel ingestion") from exc

    workbook = load_workbook(path, data_only=True, read_only=True)
    lines: list[str] = []
    for sheet in workbook.worksheets:
        lines.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            values = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
            if values:
                lines.append(" | ".join(values))
    return "\n".join(lines)


def extract_source_document(path: Path) -> SourceDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _extract_pdf_text(path)
        source_type = "pdf"
    elif suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        text = _extract_excel_text(path)
        source_type = "excel"
    else:
        raise ValueError(f"Unsupported file type for ingestion: {path.name}")

    return SourceDocument(source_path=str(path), source_type=source_type, text=text)


def discover_supported_files(input_dir: Path) -> list[Path]:
    files = [p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    return sorted(files)
