"""
ingestion models

"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDocument:
    """SourceDocument.

    Attributes:
        source_path: The path to the source document.
        source_type: The type of the source document (e.g., "pdf", "docx").
        text: The extracted text content of the source document.
    """

    source_path: str
    source_type: str
    text: str


@dataclass(frozen=True)
class ChunkRecord:
    """ChunkRecord.

    Attributes:
        chunk_id: The unique identifier for the chunk.
        source_path: The path to the source document.
        source_type: The type of the source document (e.g., "pdf", "docx").
        chunk_index: The index of the chunk within the source document.
        content: The content of the chunk.
    """

    chunk_id: str
    source_path: str
    source_type: str
    chunk_index: int
    content: str
