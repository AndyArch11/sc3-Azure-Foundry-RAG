from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class ModeOrchestrator:
    """Registry entry for mode-specific provider selection and execution."""

    provider_resolver: Callable[[], str]
    handler: Callable[[argparse.Namespace], int]


def build_arg_parser() -> argparse.ArgumentParser:
    """Create ingestion runner argument parser."""

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
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ingestion runner CLI arguments."""

    return build_arg_parser().parse_args(argv)


def normalise_control_plane_provider(provider: str | None) -> str:
    """Normalise env provider for reset/controls control-plane operations."""

    value = (provider or "azure").strip().lower() or "azure"
    if value in {"local", "dev"}:
        return "azure"
    return value


def resolve_provider_for_mode(mode: str, *, cloud_provider: str | None) -> str:
    """Resolve CLOUD_PROVIDER value for a given runner mode."""

    if mode == "azure":
        return "azure"
    if mode == "aws":
        return "aws"
    if mode == "local":
        return "local"
    if mode in {"reset", "controls"}:
        return normalise_control_plane_provider(cloud_provider)
    return "local"


def build_mode_orchestrators(
    *,
    handlers: Mapping[str, Callable[[argparse.Namespace], int]],
    cloud_provider_resolver: Callable[[], str | None],
) -> dict[str, ModeOrchestrator]:
    """Build mode orchestrator registry for main() dispatch."""

    return {
        "azure": ModeOrchestrator(
            provider_resolver=lambda: resolve_provider_for_mode(
                "azure", cloud_provider=cloud_provider_resolver()
            ),
            handler=handlers["azure"],
        ),
        "aws": ModeOrchestrator(
            provider_resolver=lambda: resolve_provider_for_mode(
                "aws", cloud_provider=cloud_provider_resolver()
            ),
            handler=handlers["aws"],
        ),
        "reset": ModeOrchestrator(
            provider_resolver=lambda: resolve_provider_for_mode(
                "reset", cloud_provider=cloud_provider_resolver()
            ),
            handler=handlers["reset"],
        ),
        "controls": ModeOrchestrator(
            provider_resolver=lambda: resolve_provider_for_mode(
                "controls", cloud_provider=cloud_provider_resolver()
            ),
            handler=handlers["controls"],
        ),
        "local": ModeOrchestrator(
            provider_resolver=lambda: resolve_provider_for_mode(
                "local", cloud_provider=cloud_provider_resolver()
            ),
            handler=handlers["local"],
        ),
    }
