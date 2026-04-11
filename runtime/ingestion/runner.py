from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from azure.core.credentials import TokenCredential

from .chunking import chunk_documents
from .extractors import discover_supported_files, extract_source_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


_CONTROLS_SOURCE_TARGET_FILENAMES = {
    "cis_controls": {
        "CIS_Controls_Version_8.xlsx",
        "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf",
    },
    "pci_dss": {
        "PCI-DSS-v4_0_1.pdf",
    },
}


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

  reset   Remove loaded indexed data on demand while keeping Azure resources.
      Clears documents from the Search index and resets indexer state.
      Optional: also clear source blobs from the storage container.
""",
    )
    parser.add_argument(
        "--mode",
        choices=["local", "azure", "reset", "controls"],
        default="local",
        help="local: client-side extraction + JSONL; azure: blob upload + Search indexer pipeline; reset: purge loaded indexed data; controls: parse/publish Corpus A frameworks",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="Directory containing source files (required for local mode; required for azure mode unless --skip-upload)",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        default=False,
        help="(azure mode) skip blob upload; files must already be in the grounding-data container",
    )
    # local mode
    parser.add_argument(
        "--output-jsonl", default="./out/chunks.jsonl", help="(local mode) JSONL output path"
    )
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument(
        "--enable-local-ocr",
        action="store_true",
        default=False,
        help="(local mode) enable OCR fallback for low-text PDFs (requires pypdfium2 + pytesseract + tesseract binary)",
    )
    parser.add_argument(
        "--local-ocr-min-text-chars",
        type=int,
        default=80,
        help="(local mode) trigger OCR fallback for PDFs whose extracted text length is below this threshold",
    )
    parser.add_argument(
        "--purge-blobs",
        action="store_true",
        default=False,
        help="(reset mode) also delete all source blobs from AZURE_STORAGE_CONTAINER_NAME",
    )
    # controls mode
    parser.add_argument(
        "--controls-framework",
        choices=[
            "all",
            "aescsf",
            "cis_controls",
            "essential_eight",
            "ism",
            "nist_csf",
            "pci_dss",
            "pspf",
        ],
        default="all",
        help="(controls mode) framework(s) to parse and publish",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        default=False,
        help="(controls mode) replace existing framework/version docs when manifest differs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="(controls mode) evaluate dedupe/publish action without writing to controls index",
    )
    parser.add_argument(
        "--no-guidance",
        action="store_true",
        default=False,
        help="(controls mode) skip supplementary guidance fetch during parsing",
    )
    parser.add_argument(
        "--controls-source-prefix",
        default=None,
        help="(controls mode) blob prefix containing staged framework source documents to download into runtime/samples before parsing",
    )
    return parser.parse_args()


def _download_controls_source_files(
    framework: str,
    source_prefix: str,
    credential: TokenCredential,
) -> list[str]:
    prefix = str(source_prefix or "").strip().strip("/")
    if not prefix:
        return []
    if framework not in _CONTROLS_SOURCE_TARGET_FILENAMES:
        raise RuntimeError(
            "--controls-source-prefix is only supported for cis_controls and pci_dss."
        )

    storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
    storage_container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "grounding-data").strip()
    if not storage_account_name:
        raise RuntimeError(
            "AZURE_STORAGE_ACCOUNT_NAME is required when --controls-source-prefix is provided."
        )

    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(storage_container_name)

    expected_filenames = set(_CONTROLS_SOURCE_TARGET_FILENAMES[framework])
    samples_dir = Path(__file__).resolve().parents[1] / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[str] = []
    found_filenames: set[str] = set()
    for blob in container.list_blobs(name_starts_with=f"{prefix}/"):
        filename = Path(blob.name).name
        if filename not in expected_filenames:
            logger.warning("Ignoring unexpected controls source blob: %s", blob.name)
            continue

        data = container.download_blob(blob.name).readall()
        (samples_dir / filename).write_bytes(data)
        found_filenames.add(filename)
        downloaded.append(filename)

    missing = sorted(expected_filenames - found_filenames)
    if missing:
        raise RuntimeError(
            "Missing staged controls source files for "
            f"{framework}: {', '.join(missing)}."
        )

    return sorted(downloaded)


def _run_local(args: argparse.Namespace) -> int:
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


def _run_azure(args: argparse.Namespace) -> int:
    from azure.identity import DefaultAzureCredential

    from .blob_uploader import upload_source_files
    from .config import IngestionConfig
    from .search_pipeline import (ensure_data_source, ensure_indexer, ensure_search_index,
                                  ensure_skillset, run_indexer, wait_for_indexer)

    try:
        config = IngestionConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()

    # Step 1: upload source documents to blob storage (optional — skip with --skip-upload)
    upload_summary = None
    if not args.skip_upload:
        if args.input_dir is None:
            print("--input-dir is required unless --skip-upload is set", file=sys.stderr)
            return 2
        input_dir = Path(args.input_dir)
        if not input_dir.exists() or not input_dir.is_dir():
            print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
            return 2
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
    else:
        logger.info(
            "Skipping blob upload (--skip-upload); files must already be in %s/%s",
            config.storage_account_name,
            config.storage_container_name,
        )

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
        "uploaded_files": len(upload_summary.uploaded) if upload_summary is not None else "skipped",
        "upload_failed": len(upload_summary.failed) if upload_summary is not None else 0,
        "indexer_status": result["status"],
        "items_processed": result["items_processed"],
        "items_failed": result["items_failed"],
        "error_message": result["error_message"],
    }
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if result["status"] == "success" else 1


def _run_reset(args: argparse.Namespace) -> int:
    from azure.identity import DefaultAzureCredential

    from .config import IngestionConfig
    from .reset import reset_loaded_data

    try:
        config = IngestionConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()

    try:
        result = reset_loaded_data(config, credential, purge_blobs=args.purge_blobs)
    except RuntimeError as exc:
        print(f"Reset error: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "mode": "reset",
                **result,
            },
            ensure_ascii=True,
        )
    )
    return 0


def _run_controls(args: argparse.Namespace) -> int:
    from azure.identity import DefaultAzureCredential

    from .controls_index import ControlsIndexConfig, ensure_controls_index
    from .controls_runner import _build_parser_registry, _selected_frameworks
    from .publish_controls import upload_controls_records

    try:
        config = ControlsIndexConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()
    ensure_controls_index(config, credential)

    source_prefix = str(getattr(args, "controls_source_prefix", "") or "").strip()
    downloaded_source_files: list[str] = []
    if source_prefix:
        try:
            downloaded_source_files = _download_controls_source_files(
                args.controls_framework,
                source_prefix,
                credential,
            )
        except Exception as exc:
            print(f"Controls source staging error: {exc}", file=sys.stderr)
            return 1

    registry = _build_parser_registry()
    selected = _selected_frameworks(args.controls_framework, registry)

    summaries: list[dict] = []
    for framework in selected:
        parser_instance = registry[framework]["factory"](fetch_guidance=(not args.no_guidance))
        records = parser_instance.parse()
        if not records:
            summaries.append(
                {
                    "framework": framework,
                    "error": "Parser returned no records",
                }
            )
            continue

        records_payload = [
            json.loads(line)
            for line in parser_instance.to_jsonl(records).splitlines()
            if line.strip()
        ]

        summary = upload_controls_records(
            config,
            credential,
            records_payload,
            replace_existing=args.replace_existing,
            dry_run=args.dry_run,
        )
        summaries.append({"framework": framework, **summary})

    payload = {
        "mode": "controls",
        "framework": args.controls_framework,
        "controls_source_prefix": source_prefix or None,
        "source_files_downloaded": downloaded_source_files,
        "replace_existing": bool(args.replace_existing),
        "dry_run": bool(args.dry_run),
        "results": summaries,
    }
    print(json.dumps(payload, ensure_ascii=True))

    if any(item.get("records_failed", 0) for item in summaries):
        return 1
    if any(item.get("error") for item in summaries):
        return 1
    return 0


def main() -> int:
    args = parse_args()
    if args.mode == "azure":
        return _run_azure(args)
    if args.mode == "reset":
        return _run_reset(args)
    if args.mode == "controls":
        return _run_controls(args)
    return _run_local(args)


if __name__ == "__main__":
    raise SystemExit(main())
