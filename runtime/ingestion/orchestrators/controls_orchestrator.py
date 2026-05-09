from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Callable

from azure.core.credentials import TokenCredential


def run_controls_aws(
    args: argparse.Namespace,
    *,
    cloud_provider: str,
    source_prefix: str,
    skip_missing_source_files: bool,
    download_controls_source_files_aws: Callable[[str, str, object, str], list[str]],
    is_missing_controls_source_error: Callable[[Exception], bool],
) -> int:
    """Run controls orchestration for AWS provider."""

    logger = logging.getLogger("ingestion-runner")
    try:
        from ...credentials import get_credential_provider
    except ImportError:
        from credentials import get_credential_provider
    from ..controls_index_aws import AWSControlsIndexConfig, ensure_controls_index_aws
    from ..controls_runner import _build_parser_registry, _selected_frameworks
    from ..publish_controls_aws import upload_controls_records_aws

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
            downloaded_source_files = download_controls_source_files_aws(
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
            parser_instance = registry[framework]["factory"](fetch_guidance=(not args.no_guidance))
            records = parser_instance.parse()
        except Exception as exc:
            if skip_missing_source_files and is_missing_controls_source_error(exc):
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


def run_controls_azure(
    args: argparse.Namespace,
    *,
    cloud_provider: str,
    source_prefix: str,
    skip_missing_source_files: bool,
    download_controls_source_files: Callable[[str, str, TokenCredential], list[str]],
    is_missing_controls_source_error: Callable[[Exception], bool],
) -> int:
    """Run controls orchestration for Azure provider."""

    from azure.identity import DefaultAzureCredential

    from ..controls_index import ControlsIndexConfig, ensure_controls_index
    from ..controls_runner import _build_parser_registry, _selected_frameworks
    from ..publish_controls import upload_controls_records

    try:
        azure_config = ControlsIndexConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    credential = DefaultAzureCredential()
    ensure_controls_index(azure_config, credential)

    downloaded_source_files: list[str] = []
    if source_prefix:
        try:
            downloaded_source_files = download_controls_source_files(
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
            if skip_missing_source_files and is_missing_controls_source_error(exc):
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
