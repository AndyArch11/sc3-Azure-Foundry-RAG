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
    """QueueMessage."""

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
    """JobRunner."""

    def run(self, message: QueueMessage) -> dict[str, Any]: ...


def _require_non_empty_string(name: str, value: object) -> str:
    """Run require non empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def validate_queue_message(payload: Mapping[str, Any]) -> QueueMessage:
    """Run validate queue message."""
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
    """Run serialise queue message."""
    return json.dumps(asdict(message), separators=(",", ":"), sort_keys=True)


def deserialise_queue_message(raw_message: str) -> QueueMessage:
    """Run deserialise queue message."""
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        raise ValueError("queue message payload must be a JSON object")
    return validate_queue_message(payload)
