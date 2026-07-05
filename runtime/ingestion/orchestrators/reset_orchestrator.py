"""
Reset ingestion orchestrator for AWS and Azure providers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def run_reset_aws(args: argparse.Namespace, *, cloud_provider: str) -> int:
    """Run reset orchestration for AWS provider.

    Args:
        args: The command-line arguments namespace.
        cloud_provider: The cloud provider name.

    Returns:
        An integer exit code.
    """

    try:
        from ...credentials import get_credential_provider
        from ...storage import get_storage_client
    except ImportError:
        from credentials import get_credential_provider
        from storage import get_storage_client
    from ..reset_aws import AWSResetConfig, reset_loaded_data_aws

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


def run_reset_azure(args: argparse.Namespace, *, cloud_provider: str) -> int:
    """Run reset orchestration for Azure provider.

    Args:
        args: The command-line arguments namespace.
        cloud_provider: The cloud provider name.

    Returns:
        An integer exit code.
    """

    from azure.identity import DefaultAzureCredential

    from ..config import IngestionConfig
    from ..reset import reset_loaded_data

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
