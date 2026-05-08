from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from azure.core.credentials import TokenCredential

try:
    from runtime.log_config import configure_logging as _configure_logging
except ModuleNotFoundError:
    # Runtime container image copies log_config.py to /app (without runtime/ package).
    from log_config import configure_logging as _configure_logging

from .chunking import chunk_documents
from .extractors import discover_supported_files, extract_source_document

_configure_logging("ingestion-runner")
logger = logging.getLogger(__name__)

# Bump this when ingestion runtime behaviour changes in ways operators may need
# to verify quickly from job logs.
INGESTION_VERSION_SIGNATURE = "ingestion-meta-safe-v2-20260417"


_CONTROLS_SOURCE_TARGET_FILENAMES = {
    "cis_controls": {
        "CIS_Controls_Version_8.xlsx",
        "CIS_Controls__v8__Critical_Security_Controls__2023_08.pdf",
    },
    "pci_dss": {
        "PCI-DSS-v4_0_1.pdf",
    },
}


def _is_missing_controls_source_error(exc: Exception) -> bool:
    """Return True when a parser error indicates missing local source files."""
    message = str(exc).lower()
    markers = (
        "not found",
        "no such file",
        "upload source documents first",
        "workbook not found",
        "pdf not found",
    )
    return any(marker in message for marker in markers)


def parse_args() -> argparse.Namespace:
    """Run parse args."""
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

  aws     Upload documents to S3 then run AWS OpenSearch indexing pipeline.
          Requires AWS credentials (via IAM role or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)
          and env vars — see runtime/README.md for the full list.

  reset   Remove loaded indexed data on demand for the selected cloud provider.
      azure: clears Search index docs and resets Azure indexer state.
      aws: clears OpenSearch index docs.
      Optional: also clear source objects from provider storage.

  controls Parse and publish Corpus A frameworks for the selected cloud provider.
      azure: publish to Azure AI Search controls index.
      aws: publish to OpenSearch controls index.
""",
    )
    parser.add_argument(
        "--mode",
        choices=["local", "azure", "aws", "reset", "controls"],
        default="local",
        help="local: client-side extraction + JSONL; azure: blob upload + Search indexer pipeline; aws: S3 upload + OpenSearch indexing; reset: purge loaded indexed data; controls: parse/publish Corpus A frameworks",
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
        help="(azure/aws mode) skip blob/S3 upload; files must already be in the storage container",
    )
    parser.add_argument(
        "--storage-container-query",
        default=None,
        help=(
            "(azure mode) optional blob virtual-directory query/prefix override for the datasource "
            "(for example corpus-b/by-dedupe/ or corpus-c/by-dedupe/)"
        ),
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
        help="(reset mode) also delete all source objects from configured storage",
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
        help="(controls mode) blob prefix containing staged framework source documents to download into runtime/samples/api/corpus-a before parsing",
    )
    parser.add_argument(
        "--skip-missing-source-files",
        action="store_true",
        default=False,
        help=(
            "(controls mode) skip frameworks whose parser requires local source files that are "
            "not present (for example cis_controls/pci_dss)"
        ),
    )
    return parser.parse_args()


def _download_controls_source_files(
    framework: str,
    source_prefix: str,
    credential: TokenCredential,
) -> list[str]:
    """Run download controls source files."""
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
    samples_dir = Path(__file__).resolve().parents[1] / "samples" / "api" / "corpus-a"
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
            "Missing staged controls source files for " f"{framework}: {', '.join(missing)}."
        )

    return sorted(downloaded)


def _download_controls_source_files_aws(
    framework: str,
    source_prefix: str,
    aws_session: object,
    s3_bucket_name: str,
) -> list[str]:
    """Download staged controls source documents from S3 into runtime samples dir."""
    prefix = str(source_prefix or "").strip().strip("/")
    if not prefix:
        return []
    if framework not in _CONTROLS_SOURCE_TARGET_FILENAMES:
        raise RuntimeError(
            "--controls-source-prefix is only supported for cis_controls and pci_dss."
        )
    bucket = str(s3_bucket_name or "").strip()
    if not bucket:
        raise RuntimeError("S3_BUCKET_NAME is required when --controls-source-prefix is provided.")

    expected_filenames = set(_CONTROLS_SOURCE_TARGET_FILENAMES[framework])
    samples_dir = Path(__file__).resolve().parents[1] / "samples" / "api" / "corpus-a"
    samples_dir.mkdir(parents=True, exist_ok=True)

    if not hasattr(aws_session, "client") or not callable(getattr(aws_session, "client")):
        raise RuntimeError("AWS session is not available for S3 source-file download.")
    import boto3 as _boto3

    _typed_session: _boto3.Session = aws_session  # type: ignore[assignment]
    s3_client = _typed_session.client("s3")

    downloaded: list[str] = []
    found_filenames: set[str] = set()
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
        for obj in page.get("Contents", []) or []:
            key = str(obj.get("Key") or "")
            filename = Path(key).name
            if filename not in expected_filenames:
                logger.warning("Ignoring unexpected controls source object: %s", key)
                continue

            data = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
            (samples_dir / filename).write_bytes(data)
            found_filenames.add(filename)
            downloaded.append(filename)

    missing = sorted(expected_filenames - found_filenames)
    if missing:
        raise RuntimeError(
            "Missing staged controls source files for " f"{framework}: {', '.join(missing)}."
        )

    return sorted(downloaded)


def _run_local(args: argparse.Namespace) -> int:
    """Run run local."""
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
    """Run run azure."""
    from azure.identity import DefaultAzureCredential

    from .blob_uploader import upload_source_files
    from .config import IngestionConfig
    from .search_pipeline import (
        ensure_data_source,
        ensure_indexer,
        ensure_search_index,
        ensure_skillset,
        run_indexer_with_rate_limit_backoff,
    )

    # Allow per-run scoping without requiring long-lived env var changes on the job.
    storage_container_query_override = str(
        getattr(args, "storage_container_query", "") or ""
    ).strip()
    if storage_container_query_override:
        os.environ["AZURE_STORAGE_CONTAINER_QUERY"] = storage_container_query_override

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
        upload_batch = (
            os.getenv("INGESTION_UPLOAD_BATCH", "").strip()
            or os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME", "").strip()
            or None
        )
        upload_summary = upload_source_files(
            storage_account_name=config.storage_account_name,
            container_name=config.storage_container_name,
            input_dir=input_dir,
            credential=credential,
            corpus=os.getenv("INGESTION_CORPUS", "b").strip() or "b",
            corpus_role=(
                os.getenv("INGESTION_CORPUS_ROLE", "narrative_guidance").strip()
                or "narrative_guidance"
            ),
            upload_source=(
                os.getenv("INGESTION_UPLOAD_SOURCE", "ingestion_runner").strip()
                or "ingestion_runner"
            ),
            uploaded_by=(
                os.getenv("INGESTION_UPLOADED_BY", "").strip()
                or os.getenv("CONTAINER_APP_JOB_NAME", "").strip()
                or "ingestion_job"
            ),
            upload_batch=upload_batch,
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
    # Brief pause to allow Azure Search to propagate the skillset update before the indexer runs.
    import time as _time

    _time.sleep(5)
    logger.warning("Skillset propagation pause complete; proceeding with indexer provisioning.")

    ensure_indexer(config, credential)

    # Step 3: trigger and wait for indexer run with rate-limit aware retry.
    logger.info("Running indexer…")
    max_attempts = max(1, int(os.getenv("INGESTION_INDEXER_MAX_ATTEMPTS", "4") or "4"))
    base_backoff_seconds = max(
        1, int(os.getenv("INGESTION_RATE_LIMIT_BASE_BACKOFF_SECONDS", "30") or "30")
    )
    max_backoff_seconds = max(
        base_backoff_seconds,
        int(os.getenv("INGESTION_RATE_LIMIT_MAX_BACKOFF_SECONDS", "300") or "300"),
    )

    result = run_indexer_with_rate_limit_backoff(
        config,
        credential,
        max_attempts=max_attempts,
        base_backoff_seconds=base_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
    )

    summary = {
        "mode": "azure",
        "storage_container_query": config.storage_container_query,
        "uploaded_files": len(upload_summary.uploaded) if upload_summary is not None else "skipped",
        "upload_failed": len(upload_summary.failed) if upload_summary is not None else 0,
        "indexer_status": result["status"],
        "items_processed": result["items_processed"],
        "items_failed": result["items_failed"],
        "error_message": result["error_message"],
        "indexer_attempt": result.get("attempt", 1),
        "indexer_max_attempts": result.get("max_attempts", max_attempts),
        "rate_limit_retry": {
            "enabled": True,
            "base_backoff_seconds": base_backoff_seconds,
            "max_backoff_seconds": max_backoff_seconds,
        },
        "scope_behaviour": (
            "Indexer run processes all blobs matching storage_container_query; "
            "item counts may exceed newly uploaded file count when upload is skipped "
            "or existing scoped blobs are reprocessed."
            if args.skip_upload and config.storage_container_query
            else None
        ),
    }
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if result["status"] == "success" else 1


def _run_aws(args: argparse.Namespace) -> int:
    """Run Corpus B ingestion on AWS: upload to S3 then extract, chunk and index into OpenSearch.

    When ``--skip-upload`` is set the files are already in S3 (e.g. they were previously
    uploaded via the query-web /api/corpus-b/ingest endpoint) and only the indexing step
    runs.  The S3 prefix to index is taken from ``--storage-container-query`` (which maps
    to ``AWS_S3_PREFIX``) or defaults to ``corpus-b/by-dedupe/``.
    """
    import tempfile

    try:
        from ..credentials import get_credential_provider
        from ..storage import get_storage_client
    except ImportError:
        from credentials import get_credential_provider
        from storage import get_storage_client
    from .chunking import chunk_documents
    from .extractors import SUPPORTED_EXTENSIONS, extract_source_document
    from .grounding_index_aws import AWSGroundingIndexConfig, ensure_grounding_index_aws
    from .publish_grounding_aws import upload_grounding_chunks_aws

    # Allow per-run scoping via environment variable.
    storage_container_query_override = str(
        getattr(args, "storage_container_query", "") or ""
    ).strip()
    if storage_container_query_override:
        os.environ["AWS_S3_PREFIX"] = storage_container_query_override

    # Step 1: get AWS credentials from abstraction layer
    credential_provider = get_credential_provider(cloud_provider="aws")
    aws_session = credential_provider.get_sdk_credential()

    corpus = os.getenv("INGESTION_CORPUS", "b").strip() or "b"
    index_prefix = os.getenv("AWS_S3_PREFIX", "").strip() or f"corpus-{corpus}/by-dedupe/"
    bucket_name = (
        os.getenv("S3_BUCKET_NAME", "").strip() or os.getenv("AWS_S3_BUCKET_NAME", "").strip()
    )
    if not bucket_name:
        print("S3_BUCKET_NAME or AWS_S3_BUCKET_NAME is required for aws mode", file=sys.stderr)
        return 1

    s3_client = get_storage_client(
        cloud_provider="aws", region_name=os.getenv("AWS_REGION"), session=aws_session
    )

    # Step 2: upload source documents to S3 (unless --skip-upload)
    uploaded_count = 0
    skipped_count = 0
    upload_failed_count = 0

    if not args.skip_upload:
        if args.input_dir is None:
            print("--input-dir is required unless --skip-upload is set", file=sys.stderr)
            return 2
        input_dir = Path(args.input_dir)
        if not input_dir.exists() or not input_dir.is_dir():
            print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
            return 2

        logger.info("Uploading source documents to AWS S3…")

        for file_path in input_dir.rglob("*"):
            if not file_path.is_file():
                continue

            try:
                content = file_path.read_bytes()
                if not content:
                    skipped_count += 1
                    continue

                relative_key = str(file_path.relative_to(input_dir))
                s3_key = f"{index_prefix}{relative_key}"

                metadata = {
                    "corpus": corpus,
                    "corpus_role": os.getenv("INGESTION_CORPUS_ROLE", "narrative_guidance")
                    or "narrative_guidance",
                    "upload_source": os.getenv("INGESTION_UPLOAD_SOURCE", "ingestion_runner")
                    or "ingestion_runner",
                    "uploaded_by": os.getenv("INGESTION_UPLOADED_BY", "")
                    or os.getenv("CONTAINER_APP_JOB_NAME", "")
                    or "ingestion_job",
                    "uploaded_at": (
                        os.getenv("INGESTION_UPLOAD_TIMESTAMP", "")
                        or os.environ.get("CONTAINER_APP_JOB_EXECUTION_TIME", "")
                    ),
                    "original_filename": file_path.name,
                }

                s3_client.put_object(bucket_name, s3_key, content, metadata=metadata)
                uploaded_count += 1
                logger.debug("Uploaded: %s", s3_key)
            except Exception as exc:
                upload_failed_count += 1
                logger.error("Failed to upload %s: %s", file_path, exc)

        logger.info(
            "S3 upload complete: %d uploaded, %d skipped, %d failed",
            uploaded_count,
            skipped_count,
            upload_failed_count,
        )

        if upload_failed_count > 0:
            return 1
    else:
        logger.info(
            "Skipping S3 upload (--skip-upload); files must already be in bucket at %s",
            index_prefix,
        )

    # Step 3: ensure grounding-index exists in OpenSearch
    try:
        grounding_config = AWSGroundingIndexConfig.from_env()
    except ValueError as exc:
        print(f"Grounding index configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        ensure_grounding_index_aws(grounding_config, aws_session)
    except Exception as exc:
        print(f"Failed to ensure grounding index: {exc}", file=sys.stderr)
        return 1

    # Step 4: list all files in S3 under the index prefix, download, extract, chunk, index
    logger.info("Listing S3 objects under prefix: %s/%s", bucket_name, index_prefix)
    try:
        all_keys = s3_client.list_objects(bucket_name, prefix=index_prefix)
    except Exception as exc:
        print(f"Failed to list S3 objects: {exc}", file=sys.stderr)
        return 1

    # Filter to supported file extensions only
    indexable_keys = [k for k in all_keys if Path(k).suffix.lower() in SUPPORTED_EXTENSIONS]
    logger.info(
        "Found %d total objects, %d with supported extensions under prefix",
        len(all_keys),
        len(indexable_keys),
    )

    if not indexable_keys:
        summary = {
            "status": "success",
            "mode": "aws",
            "storage": "s3",
            "corpus": corpus,
            "prefix": index_prefix,
            "s3_objects_found": len(all_keys),
            "documents_processed": 0,
            "chunks_indexed": 0,
            "chunks_failed": 0,
            "note": "No indexable files found under prefix.",
        }
        print(json.dumps(summary, ensure_ascii=True))
        return 0

    docs_processed = 0
    docs_failed = 0
    all_chunk_records: list[dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        for s3_key in indexable_keys:
            try:
                content = s3_client.get_object(bucket_name, s3_key)
            except Exception as exc:
                logger.error("Failed to download %s: %s", s3_key, exc)
                docs_failed += 1
                continue

            # Retrieve S3 metadata for provenance fields
            try:
                obj_meta = s3_client.get_object_metadata(bucket_name, s3_key)
            except Exception:
                obj_meta = {}

            suffix = Path(s3_key).suffix.lower()
            tmp_file = tmp_path / f"{Path(s3_key).stem}{suffix}"
            tmp_file.write_bytes(content)

            try:
                doc_tmp = extract_source_document(tmp_file)
                # Use S3 key as canonical source_path so chunk_ids are stable across re-runs.
                from .models import SourceDocument as _SourceDocument

                doc = _SourceDocument(
                    source_path=s3_key,
                    source_type=doc_tmp.source_type,
                    text=doc_tmp.text,
                )
            except Exception as exc:
                logger.warning("Text extraction failed for %s: %s", s3_key, exc)
                docs_failed += 1
                continue

            if not doc.text.strip():
                logger.warning("No text extracted from %s; skipping", s3_key)
                docs_failed += 1
                continue

            chunks = chunk_documents([doc])
            docs_processed += 1

            for chunk in chunks:
                all_chunk_records.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "source_path": s3_key,
                        "source_name": Path(s3_key).name,
                        "source_type": chunk.source_type,
                        "corpus": str(obj_meta.get("corpus") or corpus),
                        "corpus_role": str(obj_meta.get("corpus_role") or "narrative_guidance"),
                        "upload_source": str(obj_meta.get("upload_source") or ""),
                        "uploaded_by": str(obj_meta.get("uploaded_by") or ""),
                        "upload_batch": str(obj_meta.get("upload_batch") or ""),
                        "uploaded_at": str(obj_meta.get("uploaded_at") or ""),
                        "original_filename": str(
                            obj_meta.get("original_filename") or Path(s3_key).name
                        ),
                        "content_sha256": str(obj_meta.get("content_sha256") or ""),
                        "normalised_text_sha256": str(obj_meta.get("normalised_text_sha256") or ""),
                        "dedupe_hash": str(obj_meta.get("dedupe_hash") or ""),
                        "dedupe_method": str(obj_meta.get("dedupe_method") or ""),
                    }
                )

    # Step 5: bulk-index all chunks into OpenSearch
    index_result: dict = {"records_indexed": 0, "records_skipped": 0, "records_failed": 0}
    if all_chunk_records:
        try:
            index_result = upload_grounding_chunks_aws(
                grounding_config,
                aws_session,
                all_chunk_records,
                replace_existing=bool(getattr(args, "replace_existing", False)),
            )
        except Exception as exc:
            print(f"OpenSearch grounding indexing failed: {exc}", file=sys.stderr)
            return 1

    summary = {
        "status": "success",
        "mode": "aws",
        "storage": "s3",
        "corpus": corpus,
        "prefix": index_prefix,
        "s3_objects_found": len(all_keys),
        "documents_processed": docs_processed,
        "documents_failed": docs_failed,
        "chunks_total": len(all_chunk_records),
        "chunks_indexed": index_result.get("records_indexed", 0),
        "chunks_skipped": index_result.get("records_skipped", 0),
        "chunks_failed": index_result.get("records_failed", 0),
        "s3_uploads": uploaded_count,
        "s3_upload_failed": upload_failed_count,
    }
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if docs_failed == 0 and index_result.get("records_failed", 0) == 0 else 1


def _run_reset(args: argparse.Namespace) -> int:
    """Run run reset."""
    cloud_provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower() or "azure"
    if cloud_provider in {"local", "dev"}:
        cloud_provider = "azure"

    if cloud_provider == "aws":
        try:
            from ..credentials import get_credential_provider
            from ..storage import get_storage_client
        except ImportError:
            from credentials import get_credential_provider
            from storage import get_storage_client
        from .reset_aws import AWSResetConfig, reset_loaded_data_aws

        try:
            aws_config = AWSResetConfig.from_env()
        except ValueError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1

        credential_provider = get_credential_provider(cloud_provider="aws")
        aws_session = credential_provider.get_sdk_credential()
        storage_client = get_storage_client(
            cloud_provider="aws",
            region_name=os.getenv("AWS_REGION"),
            session=aws_session,
        )

        try:
            result = reset_loaded_data_aws(
                aws_config,
                aws_session,
                storage_client,
                purge_objects=args.purge_blobs,
            )
        except RuntimeError as exc:
            print(f"Reset error: {exc}", file=sys.stderr)
            return 1

        print(
            json.dumps(
                {
                    "mode": "reset",
                    "cloud_provider": cloud_provider,
                    **result,
                },
                ensure_ascii=True,
            )
        )
        return 0

    if cloud_provider != "azure":
        print(
            f"Unsupported CLOUD_PROVIDER for reset mode: {cloud_provider}. Expected azure or aws.",
            file=sys.stderr,
        )
        return 1

    from azure.identity import DefaultAzureCredential

    from .config import IngestionConfig
    from .reset import reset_loaded_data

    try:
        azure_config = IngestionConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()

    try:
        result = reset_loaded_data(azure_config, credential, purge_blobs=args.purge_blobs)
    except RuntimeError as exc:
        print(f"Reset error: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "mode": "reset",
                "cloud_provider": cloud_provider,
                **result,
            },
            ensure_ascii=True,
        )
    )
    return 0


def _run_controls(args: argparse.Namespace) -> int:
    """Run run controls."""
    cloud_provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower() or "azure"
    if cloud_provider in {"local", "dev"}:
        cloud_provider = "azure"

    from .controls_runner import _build_parser_registry, _selected_frameworks

    source_prefix = str(getattr(args, "controls_source_prefix", "") or "").strip()
    skip_missing_source_files = bool(getattr(args, "skip_missing_source_files", False))

    if cloud_provider == "aws":
        try:
            from ..credentials import get_credential_provider
        except ImportError:
            from credentials import get_credential_provider
        from .controls_index_aws import AWSControlsIndexConfig, ensure_controls_index_aws
        from .publish_controls_aws import upload_controls_records_aws

        try:
            aws_config = AWSControlsIndexConfig.from_env()
        except ValueError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1

        credential_provider = get_credential_provider(cloud_provider="aws")
        aws_session = credential_provider.get_sdk_credential()

        if hasattr(aws_session, "client") and callable(getattr(aws_session, "client")):
            try:
                caller = aws_session.client("sts").get_caller_identity()
                logger.info(
                    "AWS caller identity resolved for controls ingestion",
                    extra={
                        "aws_account_id": caller.get("Account", ""),
                        "aws_principal_arn": caller.get("Arn", ""),
                        "opensearch_endpoint": aws_config.opensearch_endpoint,
                        "controls_index_name": aws_config.controls_index_name,
                    },
                )
            except Exception as exc:
                logger.warning("Unable to resolve AWS caller identity: %s", exc)

        ensure_controls_index_aws(aws_config, aws_session)

        downloaded_source_files: list[str] = []
        if source_prefix:
            try:
                downloaded_source_files = _download_controls_source_files_aws(
                    args.controls_framework,
                    source_prefix,
                    aws_session,
                    os.getenv("S3_BUCKET_NAME", ""),
                )
            except Exception as exc:
                print(f"Controls source staging error: {exc}", file=sys.stderr)
                return 1

        registry = _build_parser_registry()
        selected = _selected_frameworks(args.controls_framework, registry)

        aws_summaries: list[dict[str, object]] = []
        for framework in selected:
            try:
                parser_instance = registry[framework]["factory"](
                    fetch_guidance=(not args.no_guidance)
                )
                records = parser_instance.parse()
            except Exception as exc:
                if skip_missing_source_files and _is_missing_controls_source_error(exc):
                    aws_summaries.append(
                        {
                            "framework": framework,
                            "action": "skipped_missing_source",
                            "reason": str(exc),
                            "records_total": 0,
                            "records_uploaded": 0,
                            "records_failed": 0,
                            "records_skipped": 0,
                        }
                    )
                    continue
                aws_summaries.append(
                    {
                        "framework": framework,
                        "error": (
                            f"Parser failed: {exc}. "
                            "For cis_controls and pci_dss, upload source documents first via /api/corpus-a/upload."
                        ),
                        "records_indexed": 0,
                        "records_failed": 1,
                    }
                )
                continue

            if not records:
                aws_summaries.append(
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

            try:
                summary = upload_controls_records_aws(
                    aws_config,
                    aws_session,
                    records_payload,
                    replace_existing=args.replace_existing,
                    dry_run=args.dry_run,
                )
                aws_summaries.append({"framework": framework, **summary})
            except Exception as exc:
                aws_summaries.append(
                    {
                        "framework": framework,
                        "error": f"Index publish failed: {exc}",
                        "records_indexed": 0,
                        "records_failed": len(records_payload),
                    }
                )

        payload = {
            "mode": "controls",
            "cloud_provider": cloud_provider,
            "framework": args.controls_framework,
            "controls_source_prefix": source_prefix or None,
            "skip_missing_source_files": skip_missing_source_files,
            "source_files_downloaded": downloaded_source_files,
            "replace_existing": bool(args.replace_existing),
            "dry_run": bool(args.dry_run),
            "results": aws_summaries,
        }
        print(json.dumps(payload, ensure_ascii=True))

        if any(item.get("records_failed", 0) for item in aws_summaries) or any(
            bool(item.get("error")) for item in aws_summaries
        ):
            return 1
        if any(item.get("error") for item in aws_summaries):
            return 1
        return 0

    if cloud_provider != "azure":
        print(
            (
                f"Unsupported CLOUD_PROVIDER for controls mode: {cloud_provider}. "
                "Expected azure or aws."
            ),
            file=sys.stderr,
        )
        return 1

    from azure.identity import DefaultAzureCredential

    from .controls_index import ControlsIndexConfig, ensure_controls_index
    from .publish_controls import upload_controls_records

    try:
        azure_config = ControlsIndexConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()
    ensure_controls_index(azure_config, credential)

    downloaded_source_files = []
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

    azure_summaries: list[dict[str, object]] = []
    for framework in selected:
        try:
            parser_instance = registry[framework]["factory"](fetch_guidance=(not args.no_guidance))
            records = parser_instance.parse()
        except Exception as exc:
            if skip_missing_source_files and _is_missing_controls_source_error(exc):
                azure_summaries.append(
                    {
                        "framework": framework,
                        "action": "skipped_missing_source",
                        "reason": str(exc),
                        "records_total": 0,
                        "records_uploaded": 0,
                        "records_failed": 0,
                        "records_skipped": 0,
                    }
                )
                continue
            azure_summaries.append(
                {
                    "framework": framework,
                    "error": (
                        f"Parser failed: {exc}. "
                        "For cis_controls and pci_dss, upload source documents first via /api/corpus-a/upload."
                    ),
                    "records_indexed": 0,
                    "records_failed": 1,
                }
            )
            continue

        if not records:
            azure_summaries.append(
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

        try:
            summary = upload_controls_records(
                azure_config,
                credential,
                records_payload,
                replace_existing=args.replace_existing,
                dry_run=args.dry_run,
            )
            azure_summaries.append({"framework": framework, **summary})
        except Exception as exc:
            azure_summaries.append(
                {
                    "framework": framework,
                    "error": f"Index publish failed: {exc}",
                    "records_indexed": 0,
                    "records_failed": len(records_payload),
                }
            )

    payload = {
        "mode": "controls",
        "cloud_provider": cloud_provider,
        "framework": args.controls_framework,
        "controls_source_prefix": source_prefix or None,
        "skip_missing_source_files": skip_missing_source_files,
        "source_files_downloaded": downloaded_source_files,
        "replace_existing": bool(args.replace_existing),
        "dry_run": bool(args.dry_run),
        "results": azure_summaries,
    }
    print(json.dumps(payload, ensure_ascii=True))

    if any(item.get("records_failed", 0) for item in azure_summaries) or any(
        bool(item.get("error")) for item in azure_summaries
    ):
        return 1
    if any(item.get("error") for item in azure_summaries):
        return 1
    return 0


def main() -> int:
    """Run main."""
    logger.warning("Ingestion version signature: %s", INGESTION_VERSION_SIGNATURE)
    args = parse_args()
    # Set CLOUD_PROVIDER env var based on --mode so abstraction factories dispatch correctly.
    if args.mode == "azure":
        os.environ["CLOUD_PROVIDER"] = "azure"
        return _run_azure(args)
    if args.mode == "aws":
        os.environ["CLOUD_PROVIDER"] = "aws"
        return _run_aws(args)
    if args.mode == "reset":
        provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower() or "azure"
        if provider in {"local", "dev"}:
            provider = "azure"
        os.environ["CLOUD_PROVIDER"] = provider
        return _run_reset(args)
    if args.mode == "controls":
        provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower() or "azure"
        if provider in {"local", "dev"}:
            provider = "azure"
        os.environ["CLOUD_PROVIDER"] = provider
        return _run_controls(args)
    os.environ["CLOUD_PROVIDER"] = "local"
    return _run_local(args)


if __name__ == "__main__":
    raise SystemExit(main())
