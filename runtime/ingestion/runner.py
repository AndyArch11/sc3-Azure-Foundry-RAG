from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from azure.core.credentials import TokenCredential

try:
    from runtime.log_config import configure_logging as _configure_logging
except ModuleNotFoundError:
    # Runtime container image copies log_config.py to /app (without runtime/ package).
    from log_config import configure_logging as _configure_logging

from .chunking import chunk_documents
from .extractors import discover_supported_files, extract_source_document
from .orchestrators.runner_facade import ModeOrchestrator

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
    from .orchestrators.controls_source_orchestrator import is_missing_controls_source_error

    return is_missing_controls_source_error(exc)


def parse_args() -> argparse.Namespace:
    """Run parse args."""
    from .orchestrators.runner_facade import parse_args as facade_parse_args

    return facade_parse_args(argv=list(sys.argv[1:]))


def _download_controls_source_files(
    framework: str,
    source_prefix: str,
    credential: TokenCredential,
) -> list[str]:
    """Run download controls source files."""
    from .orchestrators.controls_source_orchestrator import download_controls_source_files_azure

    return download_controls_source_files_azure(
        framework,
        source_prefix,
        credential,
        controls_source_target_filenames=_CONTROLS_SOURCE_TARGET_FILENAMES,
    )


def _download_controls_source_files_aws(
    framework: str,
    source_prefix: str,
    aws_session: object,
    s3_bucket_name: str,
) -> list[str]:
    """Download staged controls source documents from S3 into runtime samples dir."""
    from .orchestrators.controls_source_orchestrator import download_controls_source_files_aws

    return download_controls_source_files_aws(
        framework,
        source_prefix,
        aws_session,
        s3_bucket_name,
        controls_source_target_filenames=_CONTROLS_SOURCE_TARGET_FILENAMES,
    )


def _run_local(args: argparse.Namespace) -> int:
    """Run run local."""
    from .orchestrators.local_orchestrator import run_local

    return run_local(
        args,
        discover_supported_files=discover_supported_files,
        extract_source_document=extract_source_document,
        chunk_documents=chunk_documents,
    )


def _run_azure(args: argparse.Namespace) -> int:
    """Run run azure."""
    from .orchestrators.azure_orchestrator import run_azure

    return run_azure(args)


def _run_aws(args: argparse.Namespace) -> int:
    """Run Corpus B ingestion on AWS: upload to S3 then extract, chunk and index into OpenSearch.

    When ``--skip-upload`` is set the files are already in S3 (e.g. they were previously
    uploaded via the query-web /api/corpus-b/ingest endpoint) and only the indexing step
    runs.  The S3 prefix to index is taken from ``--storage-container-query`` (which maps
    to ``AWS_S3_PREFIX``) or defaults to ``corpus-b/by-dedupe/``.
    """
    from .orchestrators.aws_orchestrator import run_aws

    return run_aws(args)


def _run_reset_aws(args: argparse.Namespace, *, cloud_provider: str) -> int:
    """Run reset orchestration for AWS provider."""
    from .orchestrators.reset_orchestrator import run_reset_aws

    return run_reset_aws(args, cloud_provider=cloud_provider)


def _run_reset_azure(args: argparse.Namespace, *, cloud_provider: str) -> int:
    """Run reset orchestration for Azure provider."""
    from .orchestrators.reset_orchestrator import run_reset_azure

    return run_reset_azure(args, cloud_provider=cloud_provider)


def _run_reset(args: argparse.Namespace) -> int:
    """Run run reset."""
    cloud_provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower() or "azure"
    if cloud_provider in {"local", "dev"}:
        cloud_provider = "azure"

    if cloud_provider == "aws":
        return _run_reset_aws(args, cloud_provider=cloud_provider)

    if cloud_provider == "azure":
        return _run_reset_azure(args, cloud_provider=cloud_provider)

    print(
        f"Unsupported CLOUD_PROVIDER for reset mode: {cloud_provider}. Expected azure or aws.",
        file=sys.stderr,
    )
    return 1


def _run_controls(args: argparse.Namespace) -> int:
    """Run run controls."""
    cloud_provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower() or "azure"
    if cloud_provider in {"local", "dev"}:
        cloud_provider = "azure"

    source_prefix = str(getattr(args, "controls_source_prefix", "") or "").strip()
    skip_missing_source_files = bool(getattr(args, "skip_missing_source_files", False))

    if cloud_provider == "aws":
        return _run_controls_aws(
            args,
            cloud_provider=cloud_provider,
            source_prefix=source_prefix,
            skip_missing_source_files=skip_missing_source_files,
        )

    if cloud_provider == "azure":
        return _run_controls_azure(
            args,
            cloud_provider=cloud_provider,
            source_prefix=source_prefix,
            skip_missing_source_files=skip_missing_source_files,
        )

    print(
        (
            f"Unsupported CLOUD_PROVIDER for controls mode: {cloud_provider}. "
            "Expected azure or aws."
        ),
        file=sys.stderr,
    )
    return 1


def _run_controls_aws(
    args: argparse.Namespace,
    *,
    cloud_provider: str,
    source_prefix: str,
    skip_missing_source_files: bool,
) -> int:
    """Run controls orchestration for AWS provider."""
    from .orchestrators.controls_orchestrator import run_controls_aws

    return run_controls_aws(
        args,
        cloud_provider=cloud_provider,
        source_prefix=source_prefix,
        skip_missing_source_files=skip_missing_source_files,
        download_controls_source_files_aws=_download_controls_source_files_aws,
        is_missing_controls_source_error=_is_missing_controls_source_error,
    )


def _run_controls_azure(
    args: argparse.Namespace,
    *,
    cloud_provider: str,
    source_prefix: str,
    skip_missing_source_files: bool,
) -> int:
    """Run controls orchestration for Azure provider."""
    from .orchestrators.controls_orchestrator import run_controls_azure

    return run_controls_azure(
        args,
        cloud_provider=cloud_provider,
        source_prefix=source_prefix,
        skip_missing_source_files=skip_missing_source_files,
        download_controls_source_files=_download_controls_source_files,
        is_missing_controls_source_error=_is_missing_controls_source_error,
    )


def _normalise_control_plane_provider(provider: str | None) -> str:
    """Normalise env provider for reset/controls control-plane operations."""
    from .orchestrators.runner_facade import normalise_control_plane_provider

    return normalise_control_plane_provider(provider)


def _resolve_provider_for_mode(mode: str) -> str:
    """Resolve CLOUD_PROVIDER value for a given runner mode."""

    from .orchestrators.runner_facade import resolve_provider_for_mode

    return resolve_provider_for_mode(mode, cloud_provider=os.getenv("CLOUD_PROVIDER"))


def _mode_orchestrators() -> dict[str, ModeOrchestrator]:
    """Build mode orchestrator registry for main() dispatch."""

    from .orchestrators.runner_facade import build_mode_orchestrators

    return build_mode_orchestrators(
        handlers={
            "azure": _run_azure,
            "aws": _run_aws,
            "reset": _run_reset,
            "controls": _run_controls,
            "local": _run_local,
        },
        cloud_provider_resolver=lambda: os.getenv("CLOUD_PROVIDER"),
    )


def main() -> int:
    """Run main."""
    logger.warning("Ingestion version signature: %s", INGESTION_VERSION_SIGNATURE)
    args = parse_args()
    registry = _mode_orchestrators()
    orchestrator = registry.get(args.mode, registry["local"])
    os.environ["CLOUD_PROVIDER"] = orchestrator.provider_resolver()
    return orchestrator.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
