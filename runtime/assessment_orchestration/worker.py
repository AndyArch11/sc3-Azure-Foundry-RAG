from __future__ import annotations

from typing import Any

from .interfaces import OrchestratorAdapter
from .queue import QueueMessage, deserialise_queue_message, serialise_queue_message


def process_queue_message(adapter: OrchestratorAdapter, message: QueueMessage) -> dict[str, Any]:
    """Run process queue message."""
    return adapter.run_queue_message(message)


def process_queue_message_json(adapter: OrchestratorAdapter, raw_message: str) -> dict[str, Any]:
    """Run process queue message json."""
    message = deserialise_queue_message(raw_message)
    return process_queue_message(adapter, message)


def reserialise_queue_message(message: QueueMessage) -> str:
    """Run reserialise queue message."""
    return serialise_queue_message(message)
