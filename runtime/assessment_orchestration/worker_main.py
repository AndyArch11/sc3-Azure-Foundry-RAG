from __future__ import annotations

import argparse
import json
import sys

from .runtime_wiring import create_orchestrator_adapter_from_env
from .worker import process_queue_message_json


def _read_message(args: argparse.Namespace) -> str:
    """Run read message."""
    if args.message_json:
        return args.message_json
    if args.message_file:
        with open(args.message_file, "r", encoding="utf-8") as handle:
            return handle.read()
    data = sys.stdin.read()
    if not data.strip():
        raise ValueError("No queue message provided. Use --message-json, --message-file, or stdin.")
    return data


def main() -> int:
    """Run main."""
    parser = argparse.ArgumentParser(description="Assessment orchestrator worker entrypoint")
    parser.add_argument("--message-json", default="", help="Raw queue message JSON payload")
    parser.add_argument(
        "--message-file", default="", help="Path to file containing queue message JSON"
    )
    args = parser.parse_args()

    try:
        adapter = create_orchestrator_adapter_from_env()
        raw_message = _read_message(args)
        result = process_queue_message_json(adapter, raw_message)
    except Exception as exc:  # pragma: no cover - CLI error path
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
