"""CLI entry point for standards document pre-parsers.

Usage::

    # From the repository root:
    python -m runtime.ingestion.parsers.runner --framework essential_eight \\
        --output-dir ./parsed-controls/

    # Skip supplementary guidance pages (faster, no guidance_text):
    python -m runtime.ingestion.parsers.runner --framework essential_eight \\
        --output-dir ./parsed-controls/ --no-guidance

Output
------
One ``.jsonl`` file per framework version is written to ``--output-dir``.
Each line is a JSON-serialised ``RequirementRecord``.  Files are named::

    <framework_slug>_<version_slug>.jsonl

e.g. ``essential_eight_november_2023.jsonl``
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

try:
    from runtime.log_config import configure_logging as _configure_logging
except ModuleNotFoundError:
    # Runtime container image copies log_config.py to /app (without runtime/ package).
    from log_config import configure_logging as _configure_logging

_configure_logging("parsers-runner")
logger = logging.getLogger(__name__)


# Registry of supported frameworks.
# Each entry maps a CLI name → factory callable that accepts fetch_guidance kwarg.
def _build_registry():
    """Run build registry."""
    from .aescsf import AescsfParser  # noqa: PLC0415
    from .cis_controls import CisControlsParser  # noqa: PLC0415
    from .essential_eight import FRAMEWORK_VERSION, EssentialEightParser, _slugify  # noqa: PLC0415
    from .ism import IsmParser  # noqa: PLC0415
    from .nist_ai_rmf import NistAiRmfParser  # noqa: PLC0415
    from .nist_csf import FRAMEWORK_VERSION as CSF_VERSION  # noqa: PLC0415
    from .nist_csf import NistCsfParser  # noqa: PLC0415
    from .pci_dss import PciDssParser  # noqa: PLC0415
    from .pspf import PspfParser  # noqa: PLC0415

    FRAMEWORK_VERSION_SLUG = _slugify(FRAMEWORK_VERSION)

    return {
        "aescsf": {
            "factory": lambda fetch_guidance: AescsfParser(),
            "output_filename": "aescsf_v2.jsonl",
            "description": "AESCSF v2 Assessment Toolkit (354 practices across 11 domains)",
        },
        "essential_eight": {
            "factory": lambda fetch_guidance: EssentialEightParser(fetch_guidance=fetch_guidance),
            "output_filename": f"essential_eight_{FRAMEWORK_VERSION_SLUG}.jsonl",
            "description": "ASD Essential Eight Maturity Model (all three levels)",
        },
        "cis_controls": {
            "factory": lambda fetch_guidance, **kwargs: CisControlsParser(
                fetch_guidance=fetch_guidance, **kwargs
            ),
            "output_filename": "cis_controls_v8.jsonl",
            "description": "CIS Controls v8 (all safeguards)",
        },
        "ism": {
            "factory": lambda fetch_guidance: IsmParser(),
            "output_filename": "ism_latest.jsonl",
            "description": "ASD Information Security Manual (all controls, latest OSCAL release)",
        },
        "nist_ai_rmf": {
            "factory": lambda fetch_guidance: NistAiRmfParser(fetch_guidance=fetch_guidance),
            "output_filename": "nist_ai_rmf_1-0.jsonl",
            "description": "NIST AI RMF 1.0 (all control objectives)",
        },
        "nist_csf": {
            "factory": lambda fetch_guidance: NistCsfParser(fetch_guidance=fetch_guidance),
            "output_filename": f"nist_csf_{_slugify(CSF_VERSION)}.jsonl",
            "description": "NIST Cybersecurity Framework 2.0 (all 106 subcategories)",
        },
        "pci_dss": {
            "factory": lambda fetch_guidance, **kwargs: PciDssParser(
                fetch_guidance=fetch_guidance, **kwargs
            ),
            "output_filename": "pci_dss_v4_0_1.jsonl",
            "description": "PCI DSS v4.0.1 (all requirements)",
        },
        "pspf": {
            "factory": lambda fetch_guidance: PspfParser(fetch_guidance=fetch_guidance),
            "output_filename": "pspf_release_2025.jsonl",
            "description": "Australian Government PSPF Release 2025 (all requirements)",
        },
    }


def _parse_args(argv=None) -> argparse.Namespace:
    """Run parse args.

    Args:
        argv: Optional list of command-line arguments. If None, defaults to sys.argv[1:].
    Returns:
        An argparse.Namespace object containing the parsed arguments.
    """
    framework_choices = sorted(_build_registry().keys())

    parser = argparse.ArgumentParser(
        prog="python -m runtime.ingestion.parsers.runner",
        description="Pre-parse standards documents into JSONL RequirementRecord files.",
    )
    parser.add_argument(
        "--framework",
        required=True,
        choices=framework_choices,
        help="Framework to parse.",
    )
    parser.add_argument(
        "--output-dir",
        default="./parsed-controls",
        help="Directory to write JSONL output (created if absent). Default: %(default)s",
    )
    parser.add_argument(
        "--no-guidance",
        action="store_true",
        default=False,
        help="Skip fetching supplementary guidance pages (omits guidance_text field).",
    )
    parser.add_argument(
        "--cis-workbook-path",
        default="",
        help=(
            "Optional path override for CIS Controls workbook "
            "(used when --framework cis_controls)."
        ),
    )
    parser.add_argument(
        "--cis-pdf-path",
        default="",
        help=(
            "Optional path override for CIS Controls PDF " "(used when --framework cis_controls)."
        ),
    )
    parser.add_argument(
        "--pci-pdf-path",
        default="",
        help=("Optional path override for PCI DSS PDF " "(used when --framework pci_dss)."),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity. Default: %(default)s",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """Run main.

    Args:
        argv: Optional list of command-line arguments. If None, defaults to sys.argv[1:].
    Returns:
        Exit code: 0 on success, 1 on error."""
    args = _parse_args(argv)
    logging.getLogger().setLevel(args.log_level)

    registry = _build_registry()
    entry = registry[args.framework]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / entry["output_filename"]

    logger.info("Parsing framework: %s", args.framework)
    logger.info("Description: %s", entry["description"])
    logger.info("Output path: %s", output_path.resolve())

    fetch_guidance = not args.no_guidance
    if not fetch_guidance:
        logger.info("Guidance fetching disabled (--no-guidance)")

    parser_kwargs: dict[str, str] = {}
    if args.framework == "cis_controls":
        if args.cis_workbook_path.strip():
            parser_kwargs["workbook_path"] = args.cis_workbook_path.strip()
        if args.cis_pdf_path.strip():
            parser_kwargs["pdf_path"] = args.cis_pdf_path.strip()
    elif args.framework == "pci_dss":
        if args.pci_pdf_path.strip():
            parser_kwargs["pdf_path"] = args.pci_pdf_path.strip()

    parser_instance = entry["factory"](fetch_guidance=fetch_guidance, **parser_kwargs)

    try:
        records = parser_instance.parse()
    except Exception as exc:
        logger.error("Parser failed: %s", exc, exc_info=True)
        return 1

    if not records:
        logger.error("No records produced – check warnings above.")
        return 1

    jsonl_content = parser_instance.to_jsonl(records)
    output_path.write_text(jsonl_content, encoding="utf-8")

    logger.info("Wrote %d records to %s", len(records), output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
