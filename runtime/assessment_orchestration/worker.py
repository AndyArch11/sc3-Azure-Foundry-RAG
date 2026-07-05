from __future__ import annotations

from typing import Any

from .interfaces import OrchestratorAdapter
from .queue import QueueMessage, deserialise_queue_message, serialise_queue_message


def process_queue_message(adapter: OrchestratorAdapter, message: QueueMessage) -> dict[str, Any]:
    """Run process queue message.

    Args:
        adapter: The orchestrator adapter to use for processing the message.
        message: The queue message to process.
    Returns:
        A dictionary containing the result of processing the queue message.
    """
    return adapter.run_queue_message(message)


def process_queue_message_json(adapter: OrchestratorAdapter, raw_message: str) -> dict[str, Any]:
    """Run process queue message json.

    Args:
        adapter: The orchestrator adapter to use for processing the message.
        raw_message: The raw JSON string of the queue message.
    Returns:
        A dictionary containing the result of processing the queue message.
    """
    message = deserialise_queue_message(raw_message)
    return process_queue_message(adapter, message)


def reserialise_queue_message(message: QueueMessage) -> str:
    """Run reserialise queue message.

    Args:
        message: The queue message to reserialise.
    Returns:
        The JSON string of the reserialised queue message.
    """
    return serialise_queue_message(message)
