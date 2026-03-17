from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .chunking import chunk_documents
from .extractors import discover_supported_files, extract_source_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest PDF and Excel documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
modes:
  local   Extract and chunk documents locally using pypdf / openpyxl.
          Writes JSONL output.  Useful for development and unit testing.

  azure   Upload documents to blob storage then run the Azure AI Search
          indexer pipeline (DocumentExtractionSkill, OcrSkill, MergeSkill,
          SplitSkill, AzureOpenAIEmbeddingSkill).  Requires env vars —
          see runtime/README.md for the full list.
""",
    )
    parser.add_argument(
        "--mode",
        choices=["local", "azure"],
        default="local",
        help="local: client-side extraction + JSONL; azure: blob upload + Search indexer pipeline",
    )
    parser.add_argument("--input-dir", required=True, help="Directory containing source files")
    # local mode
    parser.add_argument("--output-jsonl", default="./out/chunks.jsonl", help="(local mode) JSONL output path")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    return parser.parse_args()


def _run_local(args: argparse.Namespace) -> int:
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
            docs.append(extract_source_document(path))
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


def _run_azure(args: argparse.Namespace) -> int:
    from azure.identity import DefaultAzureCredential

    from .blob_uploader import upload_source_files
    from .config import IngestionConfig
    from .search_pipeline import (
        ensure_data_source,
        ensure_indexer,
        ensure_search_index,
        ensure_skillset,
        run_indexer,
        wait_for_indexer,
    )

    try:
        config = IngestionConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()
    input_dir = Path(args.input_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    # Step 1: upload source documents to blob storage
    logger.info("Uploading source documents to blob storage…")
    upload_summary = upload_source_files(
        storage_account_name=config.storage_account_name,
        container_name=config.storage_container_name,
        input_dir=input_dir,
        credential=credential,
    )
    logger.info(
        "Upload complete: %d uploaded, %d skipped, %d failed",
        len(upload_summary.uploaded),
        len(upload_summary.skipped),
        len(upload_summary.failed),
    )
    if upload_summary.failed:
        for msg in upload_summary.failed:
            logger.error("Upload failure: %s", msg)

    # Step 2: provision index, data source, skillset, indexer
    logger.info("Ensuring Search index…")
    ensure_search_index(config, credential)

    logger.info("Ensuring data source…")
    ensure_data_source(config, credential)

    logger.info("Ensuring skillset…")
    ensure_skillset(config, credential)

    logger.info("Ensuring indexer…")
    ensure_indexer(config, credential)

    # Step 3: trigger and wait for indexer run
    logger.info("Running indexer…")
    run_indexer(config, credential)
    result = wait_for_indexer(config, credential)

    summary = {
        "mode": "azure",
        "uploaded_files": len(upload_summary.uploaded),
        "upload_failed": len(upload_summary.failed),
        "indexer_status": result["status"],
        "items_processed": result["items_processed"],
        "items_failed": result["items_failed"],
        "error_message": result["error_message"],
    }
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if result["status"] == "success" else 1


def main() -> int:
    args = parse_args()
    if args.mode == "azure":
        return _run_azure(args)
    return _run_local(args)


if __name__ == "__main__":
    raise SystemExit(main())
