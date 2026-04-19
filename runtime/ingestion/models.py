from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDocument:
    """SourceDocument."""

    source_path: str
    source_type: str
    text: str


@dataclass(frozen=True)
class ChunkRecord:
    """ChunkRecord."""

    chunk_id: str
    source_path: str
    source_type: str
    chunk_index: int
    content: str
