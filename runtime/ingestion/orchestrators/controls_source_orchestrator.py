from __future__ import annotations

import logging
import os
from pathlib import Path

from azure.core.credentials import TokenCredential


def is_missing_controls_source_error(exc: Exception) -> bool:
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


def download_controls_source_files_azure(
    framework: str,
    source_prefix: str,
    credential: TokenCredential,
    *,
    controls_source_target_filenames: dict[str, set[str]],
) -> list[str]:
    """Download staged controls source documents from Azure Blob into runtime samples dir."""

    prefix = str(source_prefix or "").strip().strip("/")
    if not prefix:
        return []
    if framework not in controls_source_target_filenames:
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

    logger = logging.getLogger("ingestion-runner")
    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    client = BlobServiceClient(account_url=account_url, credential=credential)
    container = client.get_container_client(storage_container_name)

    expected_filenames = set(controls_source_target_filenames[framework])
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


def download_controls_source_files_aws(
    framework: str,
    source_prefix: str,
    aws_session: object,
    s3_bucket_name: str,
    *,
    controls_source_target_filenames: dict[str, set[str]],
) -> list[str]:
    """Download staged controls source documents from S3 into runtime samples dir."""

    prefix = str(source_prefix or "").strip().strip("/")
    if not prefix:
        return []
    if framework not in controls_source_target_filenames:
        raise RuntimeError(
            "--controls-source-prefix is only supported for cis_controls and pci_dss."
        )
    bucket = str(s3_bucket_name or "").strip()
    if not bucket:
        raise RuntimeError("S3_BUCKET_NAME is required when --controls-source-prefix is provided.")

    logger = logging.getLogger("ingestion-runner")
    expected_filenames = set(controls_source_target_filenames[framework])
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
