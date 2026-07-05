"""
AWS orchestrator for ingestion and grounding index management.

"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def _truthy_env(name: str, default: str = "false") -> bool:
    """Return True if the environment variable is set to a truthy value, else False.

    Args:
        name: The name of the environment variable.
        default: The default value to use if the environment variable is not set.
    Returns:
        True if the environment variable is truthy, False otherwise.
    """
    raw = os.getenv(name, default).strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _embed_text_aws(text: str, session: Any, model_id: str) -> list[float]:
    """Embed text using AWS Bedrock embedding model.

    Args:
        text: The text to embed.
        session: The AWS session object.
        model_id: The ID of the Bedrock embedding model.
    Returns:
        A list of floats representing the embedding vector.
    """
    payload = {"inputText": text}
    bedrock = session.client("bedrock-runtime")
    response = bedrock.invoke_model(
        modelId=model_id,
        body=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    body_stream = response.get("body")
    if body_stream is None:
        raise RuntimeError("Bedrock embedding response body was empty")
    payload_obj = json.loads(body_stream.read())

    vector = payload_obj.get("embedding")
    if vector is None:
        by_type = payload_obj.get("embeddingsByType")
        if isinstance(by_type, dict):
            float_vectors = by_type.get("float")
            if isinstance(float_vectors, list) and float_vectors:
                vector = float_vectors[0]
    if not isinstance(vector, list) or not vector:
        raise RuntimeError("Bedrock embedding response did not include a vector")
    return [float(v) for v in vector]


def run_aws(args: argparse.Namespace) -> int:
    """Run Corpus B ingestion on AWS and publish to OpenSearch.

    Args:
        args: The command-line arguments.
    Returns:
        An integer exit code: 0 on success, 1 on error.
    """

    try:
        from runtime.credentials import get_credential_provider
        from runtime.storage import get_storage_client
    except ModuleNotFoundError:
        # In ingestion images we copy modules to /app/* without the runtime package prefix.
        from credentials import get_credential_provider
        from storage import get_storage_client

    from ..chunking import chunk_documents
    from ..extractors import SUPPORTED_EXTENSIONS, extract_source_document
    from ..grounding_index_aws import AWSGroundingIndexConfig, ensure_grounding_index_aws
    from ..publish_grounding_aws import upload_grounding_chunks_aws

    logger = logging.getLogger("ingestion-runner")

    # Allow per-run scoping via environment variable.
    storage_container_query_override = str(
        getattr(args, "storage_container_query", "") or ""
    ).strip()
    if storage_container_query_override:
        os.environ["AWS_S3_PREFIX"] = storage_container_query_override

    # Step 1: get AWS credentials from abstraction layer
    credential_provider = get_credential_provider(cloud_provider="aws")
    aws_session = credential_provider.get_sdk_credential()

    embed_on_ingest = _truthy_env("GROUNDING_EMBED_ON_INGEST", default="false")
    embedding_model_id = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "").strip()
    if embed_on_ingest and not embedding_model_id:
        logger.warning(
            "GROUNDING_EMBED_ON_INGEST is enabled but BEDROCK_EMBEDDING_MODEL_ID is empty; disabling embeddings"
        )
        embed_on_ingest = False

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
    embeddings_indexed = 0
    embedding_failures = 0

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
                from ..models import SourceDocument as _SourceDocument

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
                record = {
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

                if embed_on_ingest:
                    try:
                        vector = _embed_text_aws(
                            chunk.content[:6000], aws_session, embedding_model_id
                        )
                        record["embedding"] = vector
                        embeddings_indexed += 1
                    except Exception as exc:
                        embedding_failures += 1
                        logger.warning("Failed to embed chunk %s: %s", chunk.chunk_id, exc)

                all_chunk_records.append(record)

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
        "embeddings_indexed": embeddings_indexed,
        "embedding_failures": embedding_failures,
        "s3_uploads": uploaded_count,
        "s3_upload_failed": upload_failed_count,
    }
    print(json.dumps(summary, ensure_ascii=True))
    return 0 if docs_failed == 0 and index_result.get("records_failed", 0) == 0 else 1
