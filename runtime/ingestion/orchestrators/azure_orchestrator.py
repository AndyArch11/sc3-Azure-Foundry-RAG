from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def run_azure(args: argparse.Namespace) -> int:
    """Run Azure ingestion mode orchestration.

    Args:
        args: The command-line arguments.
    Returns:
        An integer exit code: 0 on success, 1 on error.
    """

    from azure.identity import DefaultAzureCredential

    from ..blob_uploader import upload_source_files
    from ..config import IngestionConfig
    from ..search_pipeline import (
        ensure_data_source,
        ensure_indexer,
        ensure_search_index,
        ensure_skillset,
        run_indexer_with_rate_limit_backoff,
    )

    logger = logging.getLogger("ingestion-runner")

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
