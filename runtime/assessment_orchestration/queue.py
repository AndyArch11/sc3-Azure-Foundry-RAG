"""
Queue message handling module.

This module defines the QueueMessage data structure, validation functions, and serialisation/deserialisation utilities for handling messages in the assessment orchestration queue.
It also defines the JobRunner protocol for processing queue messages.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol, cast

from .models import AssessmentJob
from .validators import validate_assessment_job

QueueMessageType = Literal["assessment_requested", "assessment_retry_requested"]
_ALLOWED_MESSAGE_TYPES = {"assessment_requested", "assessment_retry_requested"}


@dataclass(frozen=True)
class QueueMessage:
    """QueueMessage.

    Attributes:
        queue_message_id: Unique identifier for the queue message.
        message_type: Type of the queue message, must be one of the allowed message types.
        enqueued_at: Timestamp when the message was enqueued.
        correlation_id: Correlation identifier for tracing the message through the system.
        job: The assessment job associated with the queue message.
        delivery_count: Number of times the message has been delivered for processing.
        source_event_id: Optional identifier of the source event that triggered this message.
        traceparent: Optional traceparent string for distributed tracing.
        metadata: Optional dictionary containing additional metadata for the message.
    """

    queue_message_id: str
    message_type: QueueMessageType
    enqueued_at: str
    correlation_id: str
    job: AssessmentJob
    delivery_count: int = 0
    source_event_id: str = ""
    traceparent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class JobRunner(Protocol):
    """JobRunner.

    A protocol for classes that can run assessment jobs from queue messages.

    Attributes:
        message: The queue message to be processed.
    """

    def run(self, message: QueueMessage) -> dict[str, Any]: ...


def _require_non_empty_string(name: str, value: object) -> str:
    """Run require non empty string.

    Args:
        name: The name of the variable being validated (used in error messages).
        value: The value to validate.
    Returns:
        The validated string value if it is a non-empty string.
    Raises:
        ValueError: If the value is not a non-empty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def validate_queue_message(payload: Mapping[str, Any]) -> QueueMessage:
    """Run validate queue message.

    Args:
        payload: The payload to validate.
    Returns:
        The validated QueueMessage object.
    Raises:
        ValueError: If the payload is invalid.
    """
    message_type = _require_non_empty_string("message_type", payload.get("message_type"))
    if message_type not in _ALLOWED_MESSAGE_TYPES:
        raise ValueError(f"message_type must be one of {sorted(_ALLOWED_MESSAGE_TYPES)}")

    job_payload = payload.get("job")
    if not isinstance(job_payload, Mapping):
        raise ValueError("job must be an object")

    delivery_count = payload.get("delivery_count", 0)
    if not isinstance(delivery_count, int) or delivery_count < 0:
        raise ValueError("delivery_count must be a non-negative integer")

    return QueueMessage(
        queue_message_id=_require_non_empty_string(
            "queue_message_id", payload.get("queue_message_id")
        ),
        message_type=cast("Any", message_type),
        enqueued_at=_require_non_empty_string("enqueued_at", payload.get("enqueued_at")),
        correlation_id=_require_non_empty_string("correlation_id", payload.get("correlation_id")),
        job=validate_assessment_job(job_payload),
        delivery_count=delivery_count,
        source_event_id=str(payload.get("source_event_id") or "").strip(),
        traceparent=str(payload.get("traceparent") or "").strip(),
        metadata=dict(payload.get("metadata") or {}),
    )


def serialise_queue_message(message: QueueMessage) -> str:
    """Run serialise queue message.

    Args:
        message: The QueueMessage object to serialise.
    Returns:
        The serialised JSON string representation of the queue message.
    """
    return json.dumps(asdict(message), separators=(",", ":"), sort_keys=True)


def deserialise_queue_message(raw_message: str) -> QueueMessage:
    """Run deserialise queue message.

    Args:
        raw_message: The raw JSON string representation of the queue message.
    Returns:
        The deserialised QueueMessage object.
    Raises:
        ValueError: If the raw message is not a valid JSON object.
    """
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        raise ValueError("queue message payload must be a JSON object")
    return validate_queue_message(payload)
