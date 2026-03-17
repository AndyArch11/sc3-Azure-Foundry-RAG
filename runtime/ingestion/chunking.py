from __future__ import annotations

import hashlib
from typing import Iterable

from .models import ChunkRecord, SourceDocument


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def _deterministic_chunk_id(source_path: str, chunk_index: int, content: str) -> str:
    digest = hashlib.sha256(f"{source_path}:{chunk_index}:{content}".encode("utf-8")).hexdigest()
    return digest[:32]


def chunk_document(
    doc: SourceDocument,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[ChunkRecord]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap cannot be negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    cleaned = _normalise_whitespace(doc.text)
    if not cleaned:
        return []

    chunks: list[ChunkRecord] = []
    step = chunk_size - chunk_overlap
    index = 0
    chunk_idx = 0

    while index < len(cleaned):
        content = cleaned[index : index + chunk_size].strip()
        if content:
            chunks.append(
                ChunkRecord(
                    chunk_id=_deterministic_chunk_id(doc.source_path, chunk_idx, content),
                    source_path=doc.source_path,
                    source_type=doc.source_type,
                    chunk_index=chunk_idx,
                    content=content,
                )
            )
            chunk_idx += 1
        index += step

    return chunks


def chunk_documents(
    docs: Iterable[SourceDocument],
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[ChunkRecord]:
    all_chunks: list[ChunkRecord] = []
    for doc in docs:
        all_chunks.extend(
            chunk_document(doc, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        )
    return all_chunks
