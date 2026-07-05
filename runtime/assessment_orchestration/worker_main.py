"""
Worker entrypoint for assessment orchestration. This script is intended to be run as a command-line interface (CLI) tool. It reads a queue message in JSON format, either from a file, directly from the command line, or from standard input (stdin), and processes it using the assessment orchestration worker.
The script sets up logging, parses command-line arguments, and invokes the appropriate functions to handle the queue message. It is designed to be used in an Azure environment where assessment orchestration tasks are executed based on messages received from a queue.

"""

from __future__ import annotations

import argparse
import json
import sys

from runtime.log_config import configure_logging as _configure_logging

_configure_logging("worker")

from .runtime_wiring import create_orchestrator_adapter_from_env
from .worker import process_queue_message_json


def _read_message(args: argparse.Namespace) -> str:
    """Run read message.

    Args:
        args: The command-line arguments namespace containing message input options.
    Returns:
        The raw queue message as a string.
    Raises:
        ValueError: If no message is provided via command-line arguments or stdin.
    """
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
    """Run main.

    Returns:
        The exit code of the program.
    """
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
