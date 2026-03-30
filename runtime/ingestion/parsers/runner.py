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

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

# Registry of supported frameworks.
# Each entry maps a CLI name → factory callable that accepts fetch_guidance kwarg.
def _build_registry():
    from .essential_eight import (  # noqa: PLC0415
        EssentialEightParser,
        FRAMEWORK_VERSION,
        _slugify,
    )

    FRAMEWORK_VERSION_SLUG = _slugify(FRAMEWORK_VERSION)

    return {
        "essential_eight": {
            "factory": lambda fetch_guidance: EssentialEightParser(
                fetch_guidance=fetch_guidance
            ),
            "output_filename": f"essential_eight_{FRAMEWORK_VERSION_SLUG}.jsonl",
            "description": "ASD Essential Eight Maturity Model (all three levels)",
        }
    }


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m runtime.ingestion.parsers.runner",
        description="Pre-parse standards documents into JSONL RequirementRecord files.",
    )
    parser.add_argument(
        "--framework",
        required=True,
        choices=["essential_eight"],
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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity. Default: %(default)s",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
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

    parser_instance = entry["factory"](fetch_guidance=fetch_guidance)

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
