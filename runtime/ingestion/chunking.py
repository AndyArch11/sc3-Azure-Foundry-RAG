"""
Chunking module.

This module provides functionality to chunk source documents into smaller segments for processing and analysis.
It defines the `chunk_document` function, which takes a `SourceDocument` and splits it into smaller chunks based on specified chunk size and overlap parameters.
The resulting chunks are represented as `ChunkRecord` objects, which contain metadata about the chunk, including a deterministic chunk ID, source path, source type, chunk index, and the chunk content itself.
The module also includes utility functions for normalising whitespace and generating deterministic chunk IDs based on the source path, chunk index, and content.
Additionally, the `chunk_documents` function allows for chunking multiple source documents in a single operation, returning a list of all resulting `ChunkRecord` objects.
The chunking process is designed to be efficient and flexible, allowing for customisation of chunk size and overlap to suit different use cases and requirements in document processing and analysis workflows.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

from .models import ChunkRecord, SourceDocument


def _normalise_whitespace(text: str) -> str:
    """Run normalise whitespace.

    Args:
        text: The input text to normalise.

    Returns:
        The normalised text.
    """
    return " ".join(text.split())


def _deterministic_chunk_id(source_path: str, chunk_index: int, content: str) -> str:
    """Run deterministic chunk id.

    Args:
        source_path: The path of the source document.
        chunk_index: The index of the chunk within the source document.
        content: The content of the chunk.

    Returns:
        A deterministic chunk ID based on the source path, chunk index, and content.
    """
    digest = hashlib.sha256(f"{source_path}:{chunk_index}:{content}".encode("utf-8")).hexdigest()
    return digest[:32]


def chunk_document(
    doc: SourceDocument,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[ChunkRecord]:
    """Run chunk document.

    Args:
        doc: The source document to chunk.
        chunk_size: The size of each chunk. Default is 1200.
        chunk_overlap: The number of overlapping characters between chunks. Default is 200.

    Returns:
        A list of ChunkRecord objects representing the chunks of the document.
    """
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
    """Run chunk documents.

    Args:
        docs: An iterable of source documents to chunk.
        chunk_size: The size of each chunk. Default is 1200.
        chunk_overlap: The number of overlapping characters between chunks. Default is 200.

    Returns:
        A list of ChunkRecord objects representing the chunks of all documents.
    """
    all_chunks: list[ChunkRecord] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc, chunk_size=chunk_size, chunk_overlap=chunk_overlap))
    return all_chunks
