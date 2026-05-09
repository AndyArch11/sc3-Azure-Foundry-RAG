from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from ..models import ChunkRecord, SourceDocument


class _ExtractSourceDocumentFn(Protocol):
    """Callable shape for source extraction function."""

    def __call__(
        self,
        path: Path,
        *,
        enable_ocr: bool,
        ocr_min_text_chars: int,
    ) -> SourceDocument: ...


class _ChunkDocumentsFn(Protocol):
    """Callable shape for chunking function."""

    def __call__(
        self,
        docs: Iterable[SourceDocument],
        *,
        chunk_size: int,
        chunk_overlap: int,
    ) -> Sequence[ChunkRecord]: ...


def run_local(
    args: argparse.Namespace,
    *,
    discover_supported_files: Callable[[Path], list[Path]],
    extract_source_document: _ExtractSourceDocumentFn,
    chunk_documents: _ChunkDocumentsFn,
) -> int:
    """Run local ingestion mode orchestration."""

    if args.input_dir is None:
        print("--input-dir is required for local mode", file=sys.stderr)
        return 2
    input_dir = Path(args.input_dir)
    output_path = Path(args.output_jsonl)

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    files = discover_supported_files(input_dir)
    if not files:
        print("No supported PDF/Excel files found", file=sys.stderr)
        return 3

    processed = 0
    failed = 0
    docs = []

    for path in files:
        try:
            docs.append(
                extract_source_document(
                    path,
                    enable_ocr=args.enable_local_ocr,
                    ocr_min_text_chars=args.local_ocr_min_text_chars,
                )
            )
            processed += 1
        except Exception as exc:  # pragma: no cover - defensive summary reporting
            failed += 1
            print(f"Failed to process {path}: {exc}", file=sys.stderr)

    chunks = chunk_documents(
        docs,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(
                json.dumps(
                    {
                        "chunk_id": chunk.chunk_id,
                        "source_path": chunk.source_path,
                        "source_type": chunk.source_type,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                    },
                    ensure_ascii=True,
                )
            )
            f.write("\n")

    print(
        json.dumps(
            {
                "mode": "local",
                "processed_files": processed,
                "failed_files": failed,
                "chunk_count": len(chunks),
                "output_jsonl": str(output_path),
            },
            ensure_ascii=True,
        )
    )
    return 0
