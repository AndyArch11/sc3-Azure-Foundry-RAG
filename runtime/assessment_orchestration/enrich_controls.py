"""
Enrich control documents with applicability metadata.
Can be used as a standalone script for bulk control enrichment at ingestion time.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from runtime.assessment_orchestration.control_applicability import enrich_control_with_applicability


def enrich_controls_file(
    input_file: str,
    output_file: str,
    skip_if_present: bool = True,
) -> dict[str, Any]:
    """
    Read controls from input JSONL, enrich with applicability metadata, write to output.

    Args:
        input_file: Input JSONL file path
        output_file: Output JSONL file path (or "-" for stdout)
        skip_if_present: If True, skip enrichment for controls that already have applicability fields

    Returns: Statistics dict with counts
    """
    enriched = 0
    skipped = 0
    errors = 0

    output_handle = sys.stdout if output_file == "-" else open(output_file, "w")

    try:
        with open(input_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    control = json.loads(line)

                    if skip_if_present and "control_applicability_scope" in control:
                        skipped += 1
                        output_handle.write(json.dumps(control) + "\n")
                        continue

                    enriched_control = enrich_control_with_applicability(control)
                    enriched += 1
                    output_handle.write(json.dumps(enriched_control) + "\n")
                except Exception as exc:
                    errors += 1
                    print(f"Error processing line: {exc}", file=sys.stderr)
    finally:
        if output_file != "-":
            output_handle.close()

    return {
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
        "total": enriched + skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enrich control documents with applicability metadata."
    )
    parser.add_argument(
        "input_file",
        help="Input JSONL file with controls",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_file",
        default="-",
        help="Output JSONL file (default: stdout)",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Enrich even if applicability fields already present",
    )

    args = parser.parse_args(argv)

    try:
        stats = enrich_controls_file(
            args.input_file,
            args.output_file,
            skip_if_present=not args.no_skip,
        )
        print(json.dumps(stats, indent=2), file=sys.stderr)
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
