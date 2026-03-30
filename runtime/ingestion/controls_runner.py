from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from azure.identity import DefaultAzureCredential

from .controls_index import ControlsIndexConfig, ensure_controls_index
from .publish_controls import load_controls_jsonl, upload_controls_records

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _build_parser_registry() -> dict[str, dict]:
    from .parsers.essential_eight import (  # noqa: PLC0415
        EssentialEightParser,
        FRAMEWORK_VERSION,
        _slugify,
    )

    return {
        "essential_eight": {
            "factory": lambda fetch_guidance: EssentialEightParser(
                fetch_guidance=fetch_guidance
            ),
            "output_filename": f"essential_eight_{_slugify(FRAMEWORK_VERSION)}.jsonl",
        }
    }


def parse_args() -> argparse.Namespace:
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
        choices=["essential_eight"],
        default="essential_eight",
        help="Framework parser to run when mode includes parse",
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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _run_parse(framework: str, output_dir: Path, no_guidance: bool) -> Path:
    registry = _build_parser_registry()
    entry = registry[framework]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / entry["output_filename"]

    parser_instance = entry["factory"](fetch_guidance=(not no_guidance))
    records = parser_instance.parse()
    if not records:
        raise RuntimeError("Parser returned no records")

    output_path.write_text(parser_instance.to_jsonl(records), encoding="utf-8")
    logger.info("Parsed %d records to %s", len(records), output_path)
    return output_path


def _run_publish(config: ControlsIndexConfig, jsonl_path: Path, batch_size: int) -> dict:
    credential = DefaultAzureCredential()

    ensure_controls_index(config, credential)
    records = load_controls_jsonl(jsonl_path)
    result = upload_controls_records(
        config,
        credential,
        records,
        batch_size=batch_size,
    )
    result["jsonl_path"] = str(jsonl_path)
    return result


def main() -> int:
    args = parse_args()
    logging.getLogger().setLevel(args.log_level)

    config = ControlsIndexConfig.from_env()

    if args.mode == "ensure-index":
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

    parsed_path: Path | None = None
    if args.mode in {"parse", "parse-and-publish"}:
        parsed_path = _run_parse(
            framework=args.framework,
            output_dir=Path(args.output_dir),
            no_guidance=args.no_guidance,
        )

    if args.mode == "parse":
        print(
            json.dumps(
                {
                    "mode": "parse",
                    "framework": args.framework,
                    "output_jsonl": str(parsed_path),
                },
                ensure_ascii=True,
            )
        )
        return 0

    jsonl_path = Path(args.input_jsonl) if args.input_jsonl else parsed_path
    if jsonl_path is None:
        raise RuntimeError("No JSONL source available for publish mode")

    summary = _run_publish(config, jsonl_path, args.batch_size)
    print(json.dumps({"mode": args.mode, **summary}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
