from __future__ import annotations

import pytest

from runtime.assessment_orchestration.models import AssessmentJob
from runtime.assessment_orchestration.queue import (
    QueueMessage,
    deserialise_queue_message,
    serialise_queue_message,
    validate_queue_message,
)
from runtime.assessment_orchestration.validators import (
    validate_assessment_job,
    validate_identity_mode,
)


def test_validate_identity_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="identity_mode"):
        validate_identity_mode("service")


def test_validate_assessment_job_builds_dataclass() -> None:
    job = validate_assessment_job(
        {
            "job_id": "job-1",
            "source_type": "manual_request",
            "provider": "sharepoint",
            "target_id": "page-1",
            "target_url": "https://example/page-1",
            "trigger_type": "user_request",
            "request_identity_mode": "delegated",
            "delivery_policy": "inline_else_email",
            "correlation_id": "corr-1",
        }
    )

    assert isinstance(job, AssessmentJob)
    assert job.request_identity_mode == "delegated"


def test_queue_message_roundtrip_preserves_job() -> None:
    message = QueueMessage(
        queue_message_id="msg-1",
        message_type="assessment_requested",
        enqueued_at="2026-04-02T00:00:00Z",
        correlation_id="corr-1",
        job=AssessmentJob(
            job_id="job-1",
            source_type="provider_event",
            provider="confluence",
            target_id="page-1",
            target_url="https://example/wiki/page-1",
            trigger_type="mention",
            request_identity_mode="app_only",
            delivery_policy="inline_else_email",
            correlation_id="corr-1",
        ),
        delivery_count=1,
        source_event_id="evt-1",
    )

    restored = deserialise_queue_message(serialise_queue_message(message))

    assert restored.queue_message_id == "msg-1"
    assert restored.job.target_id == "page-1"
    assert restored.job.request_identity_mode == "app_only"


def test_validate_queue_message_requires_job_object() -> None:
    with pytest.raises(ValueError, match="job"):
        validate_queue_message(
            {
                "queue_message_id": "msg-1",
                "message_type": "assessment_requested",
                "enqueued_at": "2026-04-02T00:00:00Z",
                "correlation_id": "corr-1",
                "job": "not-an-object",
            }
        )
