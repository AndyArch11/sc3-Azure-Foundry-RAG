from pathlib import Path

import pytest

from runtime.ingestion.chunking import chunk_document
from runtime.ingestion.extractors import extract_source_document
from runtime.ingestion.models import SourceDocument


def test_chunking_is_deterministic_for_same_input() -> None:
    doc = SourceDocument(
        source_path="fixtures/sample.pdf",
        source_type="pdf",
        text="A " * 2000,
    )

    run1 = chunk_document(doc, chunk_size=300, chunk_overlap=50)
    run2 = chunk_document(doc, chunk_size=300, chunk_overlap=50)

    assert [c.chunk_id for c in run1] == [c.chunk_id for c in run2]
    assert [c.content for c in run1] == [c.content for c in run2]


def test_chunking_validates_overlap_rules() -> None:
    doc = SourceDocument(source_path="x", source_type="pdf", text="hello")

    try:
        chunk_document(doc, chunk_size=100, chunk_overlap=100)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "chunk_overlap" in str(exc)


@pytest.mark.sample_fixtures
@pytest.mark.parametrize(
    "fixture_name,expected_type",
    [
        ("AESCSF-Framework-Core.xlsx", "excel"),
        ("PROTECT - Essential Eight Maturity Model (November 2023).pdf", "pdf"),
    ],
)
def test_chunking_against_runtime_samples(fixture_name: str, expected_type: str) -> None:
    sample_path = Path(__file__).resolve().parents[2] / "runtime" / "samples" / fixture_name
    assert sample_path.exists(), f"missing sample fixture: {sample_path}"

    doc = extract_source_document(sample_path)
    assert doc.source_type == expected_type
    assert doc.source_path.endswith(fixture_name)

    chunks = chunk_document(doc, chunk_size=600, chunk_overlap=100)
    assert isinstance(chunks, list)

    # If extracted text exists, ensure deterministic chunk output on repeated runs.
    rerun_chunks = chunk_document(doc, chunk_size=600, chunk_overlap=100)
    assert [c.chunk_id for c in chunks] == [c.chunk_id for c in rerun_chunks]
