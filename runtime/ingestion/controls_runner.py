"""
Controls runner for parsing and publishing controls to Azure Cognitive Search.
This script provides a command-line interface to parse various security and compliance frameworks
into a structured JSONL format and publish them to a dedicated Azure Cognitive Search index.

"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

try:
    from runtime.log_config import configure_logging as _configure_logging
except ModuleNotFoundError:
    # Runtime container image copies log_config.py to /app (without runtime/ package).
    from log_config import configure_logging as _configure_logging

_configure_logging("controls-runner")
logger = logging.getLogger(__name__)


def ensure_controls_index(config, credential) -> None:
    """Lazy wrapper retained for compatibility with test monkeypatching.
    Args:
        config: The ControlsIndexConfig instance containing Azure Cognitive Search configuration.
        credential: The TokenCredential instance used for authenticating with Azure Cognitive Search.
    """
    from .controls_index import ensure_controls_index as _ensure_controls_index

    _ensure_controls_index(config, credential)


def DefaultAzureCredential() -> Any:
    """Lazy wrapper retained for compatibility with test monkeypatching.

    Returns:
        An instance of DefaultAzureCredential from azure.identity for authenticating with Azure services.
    """
    from azure.identity import DefaultAzureCredential as _DefaultAzureCredential

    return _DefaultAzureCredential()


def _is_missing_source_error(exc: Exception) -> bool:
    """Run is missing source error.

    Args:
        exc: The exception instance to check.

    Returns:
        True if the exception indicates a missing source error, False otherwise.
    """
    message = str(exc).lower()
    return "not found" in message or "no such file" in message


def _build_parser_registry() -> dict[str, dict]:
    """Run build parser registry.

    Returns:
        A dictionary mapping framework names to their corresponding parser factory functions and output filenames.
    """
    from .parsers.aescsf import AescsfParser  # noqa: PLC0415
    from .parsers.cis_controls import CisControlsParser  # noqa: PLC0415
    from .parsers.essential_eight import EssentialEightParser  # noqa: PLC0415
    from .parsers.essential_eight import FRAMEWORK_VERSION, _slugify
    from .parsers.ism import IsmParser  # noqa: PLC0415
    from .parsers.nist_ai_rmf import NistAiRmfParser  # noqa: PLC0415
    from .parsers.nist_csf import FRAMEWORK_VERSION as CSF_VERSION  # noqa: PLC0415
    from .parsers.nist_csf import NistCsfParser  # noqa: PLC0415
    from .parsers.pci_dss import PciDssParser  # noqa: PLC0415
    from .parsers.pspf import PspfParser  # noqa: PLC0415

    return {
        "aescsf": {
            "factory": lambda fetch_guidance: AescsfParser(),
            "output_filename": "aescsf_v2.jsonl",
        },
        "cis_controls": {
            "factory": lambda fetch_guidance: CisControlsParser(),
            "output_filename": "cis_controls_v8.jsonl",
            "optional_when_all": True,
        },
        "pci_dss": {
            "factory": lambda fetch_guidance: PciDssParser(),
            "output_filename": "pci_dss_v4_0_1.jsonl",
            "optional_when_all": True,
        },
        "pspf": {
            "factory": lambda fetch_guidance: PspfParser(),
            "output_filename": "pspf_release_2025.jsonl",
        },
        "essential_eight": {
            "factory": lambda fetch_guidance: EssentialEightParser(fetch_guidance=fetch_guidance),
            "output_filename": f"essential_eight_{_slugify(FRAMEWORK_VERSION)}.jsonl",
        },
        "ism": {
            "factory": lambda fetch_guidance: IsmParser(),
            "output_filename": "ism_latest.jsonl",
        },
        "nist_csf": {
            "factory": lambda fetch_guidance: NistCsfParser(fetch_guidance=fetch_guidance),
            "output_filename": f"nist_csf_{_slugify(CSF_VERSION)}.jsonl",
        },
        "nist_ai_rmf": {
            "factory": lambda fetch_guidance: NistAiRmfParser(fetch_guidance=fetch_guidance),
            "output_filename": "nist_ai_rmf_1-0.jsonl",
        },
    }


def _selected_frameworks(framework: str, registry: dict[str, dict]) -> list[str]:
    """Run selected frameworks.

    Args:
        framework: The framework name specified by the user.
        registry: The dictionary mapping framework names to their corresponding parser factory functions and output filenames.

    Returns:
        A list of selected framework names.
    """
    if framework == "all":
        return sorted(registry.keys())
    return [framework]


def parse_args() -> argparse.Namespace:
    """Run parse args.

    Returns:
        An argparse.Namespace object containing the parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Parse controls JSONL and publish to dedicated Azure AI Search controls index"
    )
    parser.add_argument(
        "--mode",
        choices=["parse", "publish", "parse-and-publish", "ensure-index"],
        default="parse-and-publish",
        help="parse: generate JSONL only; publish: publish existing JSONL; parse-and-publish: do both; ensure-index: create/update index only",
    )
    parser.add_argument(
        "--framework",
        choices=[
            "all",
            "aescsf",
            "cis_controls",
            "essential_eight",
            "ism",
            "nist_ai_rmf",
            "nist_csf",
            "pci_dss",
            "pspf",
        ],
        default="essential_eight",
        help="Framework parser to run when mode includes parse. Use 'all' to run every supported parser",
    )
    parser.add_argument(
        "--output-dir",
        default="./parsed-controls",
        help="Directory for parsed JSONL output",
    )
    parser.add_argument(
        "--input-jsonl",
        default=None,
        help="Path to existing JSONL for publish mode. If omitted in parse-and-publish, parser output is used",
    )
    parser.add_argument(
        "--no-guidance",
        action="store_true",
        default=False,
        help="Skip supplementary guidance fetch while parsing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Batch size for Search document upload",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        default=False,
        help="When publishing controls, replace existing docs for same framework/version if manifest differs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview dedupe/publish action without writing to the controls index",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--search-endpoint",
        default=None,
        help="Azure AI Search endpoint override (falls back to AZURE_SEARCH_ENDPOINT)",
    )
    parser.add_argument(
        "--controls-index-name",
        default=None,
        help="Controls index name override (falls back to AZURE_SEARCH_CONTROLS_INDEX_NAME or controls-index)",
    )
    return parser.parse_args()


def _resolve_controls_index_config(args: argparse.Namespace):
    """Run resolve controls index config.

    Args:
        args: The argparse.Namespace object containing the parsed command-line arguments.

    Returns:
        A ControlsIndexConfig object with the resolved configuration.
    """
    from .controls_index import ControlsIndexConfig

    if args.search_endpoint:
        return ControlsIndexConfig(
            search_endpoint=str(args.search_endpoint).strip(),
            controls_index_name=(
                str(args.controls_index_name).strip()
                if args.controls_index_name
                else "controls-index"
            ),
        )

    config = ControlsIndexConfig.from_env()
    if args.controls_index_name:
        return ControlsIndexConfig(
            search_endpoint=config.search_endpoint,
            controls_index_name=str(args.controls_index_name).strip(),
        )
    return config


def _run_parse(framework: str, output_dir: Path, no_guidance: bool) -> dict[str, Path]:
    """Run run parse.

    Args:
        framework: The framework name specified by the user.
        output_dir: The directory where the parsed output will be saved.
        no_guidance: Whether to skip supplementary guidance fetch.

    Returns:
        A dictionary mapping framework names to their corresponding output file paths.
    """
    outputs, _skipped = _run_parse_detailed(
        framework=framework,
        output_dir=output_dir,
        no_guidance=no_guidance,
    )
    return outputs


def _run_parse_detailed(
    framework: str,
    output_dir: Path,
    no_guidance: bool,
) -> tuple[dict[str, Path], list[dict[str, str]]]:
    """Run run parse detailed.

    Args:
        framework: The framework name specified by the user.
        output_dir: The directory where the parsed output will be saved.
        no_guidance: Whether to skip supplementary guidance fetch.

    Returns:
        A tuple containing:
            - A dictionary mapping framework names to their corresponding output file paths.
            - A list of dictionaries containing information about skipped frameworks.
    """
    registry = _build_parser_registry()
    frameworks = _selected_frameworks(framework, registry)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    skipped: list[dict[str, str]] = []

    for selected in frameworks:
        entry = registry[selected]
        output_path = output_dir / entry["output_filename"]

        parser_instance = entry["factory"](fetch_guidance=(not no_guidance))
        try:
            records = parser_instance.parse()
        except Exception as exc:
            if (
                framework == "all"
                and entry.get("optional_when_all")
                and _is_missing_source_error(exc)
            ):
                logger.warning(
                    "Skipping optional framework '%s': %s",
                    selected,
                    exc,
                )
                skipped.append(
                    {
                        "framework": selected,
                        "reason": str(exc),
                    }
                )
                continue
            raise
        if not records:
            raise RuntimeError(f"Parser '{selected}' returned no records")

        output_path.write_text(parser_instance.to_jsonl(records), encoding="utf-8")
        logger.info("Parsed %d records for %s to %s", len(records), selected, output_path)
        outputs[selected] = output_path

    return outputs, skipped


def _run_publish(
    config,
    jsonl_path: Path,
    batch_size: int,
    *,
    replace_existing: bool,
    dry_run: bool,
) -> dict:
    """Run run publish.

    Args:
        config: The ControlsIndexConfig instance containing Azure Cognitive Search configuration.
        jsonl_path: The path to the JSONL file to publish.
        batch_size: The number of records to upload in each batch.
        replace_existing: Whether to replace existing documents for the same framework/version if the manifest differs.
        dry_run: If True, preview the dedupe/publish action without writing to the controls index.

    Returns:
        A dictionary containing the results of the publish operation, including counts of uploaded, skipped, and failed records.
    """
    from .controls_index import ensure_controls_index
    from .publish_controls import load_controls_jsonl, upload_controls_records

    credential = DefaultAzureCredential()

    ensure_controls_index(config, credential)
    records = load_controls_jsonl(jsonl_path)
    result = upload_controls_records(
        config,
        credential,
        records,
        batch_size=batch_size,
        replace_existing=replace_existing,
        dry_run=dry_run,
    )
    result["jsonl_path"] = str(jsonl_path)
    return result


def _log_framework_all_summary(
    *,
    mode: str,
    parsed_outputs: dict[str, Path],
    skipped_frameworks: list[dict[str, str]],
) -> None:
    """Run log framework all summary.

    Args:
        mode: The mode of operation (parse, publish, parse-and-publish).
        parsed_outputs: A dictionary mapping framework names to their corresponding output file paths.
        skipped_frameworks: A list of dictionaries containing information about skipped frameworks.
    """
    parsed_names = sorted(parsed_outputs.keys())
    skipped_names = [entry.get("framework", "unknown") for entry in skipped_frameworks]
    logger.info(
        "Framework '%s' summary for --framework all: parsed=%d (%s), skipped=%d (%s)",
        mode,
        len(parsed_names),
        ", ".join(parsed_names) if parsed_names else "none",
        len(skipped_names),
        ", ".join(skipped_names) if skipped_names else "none",
    )


def main() -> int:
    """Run main.

    Returns:
        An integer exit code (0 for success, non-zero for failure).
    """
    args = parse_args()
    logging.getLogger().setLevel(args.log_level)

    if args.mode == "ensure-index":
        config = _resolve_controls_index_config(args)
        ensure_controls_index(config, DefaultAzureCredential())
        print(
            json.dumps(
                {
                    "mode": "ensure-index",
                    "index_name": config.controls_index_name,
                },
                ensure_ascii=True,
            )
        )
        return 0

    parsed_outputs: dict[str, Path] = {}
    skipped_frameworks: list[dict[str, str]] = []
    if args.mode in {"parse", "parse-and-publish"}:
        parsed_outputs, skipped_frameworks = _run_parse_detailed(
            framework=args.framework,
            output_dir=Path(args.output_dir),
            no_guidance=args.no_guidance,
        )
        if args.framework == "all":
            _log_framework_all_summary(
                mode=args.mode,
                parsed_outputs=parsed_outputs,
                skipped_frameworks=skipped_frameworks,
            )

    if args.mode == "parse":
        if args.framework == "all":
            payload = {
                "mode": "parse",
                "framework": args.framework,
                "output_jsonls": {k: str(v) for k, v in parsed_outputs.items()},
                "parsed_frameworks": sorted(parsed_outputs.keys()),
                "skipped_frameworks": skipped_frameworks,
            }
        else:
            selected = args.framework
            payload = {
                "mode": "parse",
                "framework": selected,
                "output_jsonl": str(parsed_outputs[selected]),
            }
        print(
            json.dumps(
                payload,
                ensure_ascii=True,
            )
        )
        return 0

    if args.mode == "publish":
        config = _resolve_controls_index_config(args)
        jsonl_path = Path(args.input_jsonl) if args.input_jsonl else None
        if jsonl_path is None:
            raise RuntimeError("No JSONL source available for publish mode")
        summary = _run_publish(
            config,
            jsonl_path,
            args.batch_size,
            replace_existing=args.replace_existing,
            dry_run=args.dry_run,
        )
        print(json.dumps({"mode": args.mode, **summary}, ensure_ascii=True))
        return 0

    # parse-and-publish mode
    config = _resolve_controls_index_config(args)
    if args.framework == "all":
        summaries = []
        for framework_name, jsonl_path in parsed_outputs.items():
            summary = _run_publish(
                config,
                jsonl_path,
                args.batch_size,
                replace_existing=args.replace_existing,
                dry_run=args.dry_run,
            )
            summaries.append({"framework": framework_name, **summary})
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "framework": args.framework,
                    "results": summaries,
                    "parsed_frameworks": sorted(parsed_outputs.keys()),
                    "skipped_frameworks": skipped_frameworks,
                },
                ensure_ascii=True,
            )
        )
        return 0

    selected_path = parsed_outputs[args.framework]
    summary = _run_publish(
        config,
        selected_path,
        args.batch_size,
        replace_existing=args.replace_existing,
        dry_run=args.dry_run,
    )
    print(json.dumps({"mode": args.mode, **summary}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
