from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import urlparse

from .models import AssessmentJob, IdentityMode
from .queue import QueueMessage, validate_queue_message
from .validators import validate_assessment_job


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _infer_provider_from_url(target_url: str) -> str:
    host = (urlparse(target_url).hostname or "").lower()
    if "atlassian.net" in host or "confluence" in host:
        return "confluence"
    if "sharepoint.com" in host or "office.com" in host:
        return "sharepoint"
    return "email"


def _first_non_empty(payload: Mapping[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _stable_correlation(source_event_id: str, target_url: str) -> str:
    base = f"{source_event_id}|{target_url}".encode("utf-8")
    return hashlib.sha256(base).hexdigest()[:32]


def build_assessment_job_from_provider_event(
    payload: Mapping[str, Any],
    *,
    provider_hint: str = "",
    request_identity_mode: IdentityMode = "app_only",
    delivery_policy: str = "inline_else_email",
) -> AssessmentJob:
    target_url = _first_non_empty(payload, ["target_url", "url", "canonical_url"])
    if not target_url:
        raise ValueError("Provider event payload must include target_url")

    provider = provider_hint.strip().lower() or _infer_provider_from_url(target_url)
    target_id = _first_non_empty(payload, ["target_id", "id", "page_id", "item_id"])
    if not target_id:
        raise ValueError("Provider event payload must include target_id")

    source_event_id = _first_non_empty(payload, ["event_id", "message_id", "source_event_id"]) or str(uuid.uuid4())
    correlation_id = _first_non_empty(payload, ["correlation_id"]) or _stable_correlation(source_event_id, target_url)
    trigger_type = _first_non_empty(payload, ["trigger_type"]) or "mention"

    job_payload = {
        "job_id": _first_non_empty(payload, ["job_id"]) or str(uuid.uuid4()),
        "source_type": _first_non_empty(payload, ["source_type"]) or "provider_event",
        "provider": provider,
        "target_id": target_id,
        "target_url": target_url,
        "trigger_type": trigger_type,
        "request_identity_mode": request_identity_mode,
        "delivery_policy": _first_non_empty(payload, ["delivery_policy"]) or delivery_policy,
        "correlation_id": correlation_id,
        "requester_id": _first_non_empty(payload, ["requester_id", "requester_principal_id"]),
        "requester_email": _first_non_empty(payload, ["requester_email", "mentioner_email"]),
        "metadata": {
            "source_event_id": source_event_id,
            "provider_raw_event_type": _first_non_empty(payload, ["event_type"]),
        },
    }
    return validate_assessment_job(job_payload)


def build_assessment_job_from_email_notification(
    parsed_email_notification: Mapping[str, Any],
    *,
    request_identity_mode: IdentityMode = "app_only",
    delivery_policy: str = "inline_else_email",
) -> AssessmentJob:
    target_reference = _first_non_empty(parsed_email_notification, ["target_reference", "target_url", "url"])
    if not target_reference:
        raise ValueError("Email notification must include target_reference")

    provider = _first_non_empty(parsed_email_notification, ["provider"]).lower() or _infer_provider_from_url(target_reference)
    target_id = _first_non_empty(parsed_email_notification, ["target_id", "id"])
    if not target_id:
        target_id = hashlib.sha256(target_reference.encode("utf-8")).hexdigest()[:24]

    source_event_id = _first_non_empty(parsed_email_notification, ["message_id", "source_event_id"]) or str(uuid.uuid4())
    correlation_id = _first_non_empty(parsed_email_notification, ["correlation_id"]) or _stable_correlation(source_event_id, target_reference)

    job_payload = {
        "job_id": str(uuid.uuid4()),
        "source_type": "email_notification",
        "provider": provider,
        "target_id": target_id,
        "target_url": target_reference,
        "trigger_type": "email_notification",
        "request_identity_mode": request_identity_mode,
        "delivery_policy": delivery_policy,
        "correlation_id": correlation_id,
        "requester_id": _first_non_empty(parsed_email_notification, ["requester_id"]),
        "requester_email": _first_non_empty(parsed_email_notification, ["requester_email", "mentioner_email"]),
        "metadata": {
            "source_event_id": source_event_id,
            "mailbox_id": _first_non_empty(parsed_email_notification, ["mailbox_id"]),
        },
    }
    return validate_assessment_job(job_payload)


def build_queue_message(
    job: AssessmentJob,
    *,
    message_type: str = "assessment_requested",
    source_event_id: str = "",
    traceparent: str = "",
) -> QueueMessage:
    payload = {
        "queue_message_id": str(uuid.uuid4()),
        "message_type": message_type,
        "enqueued_at": _utc_now_iso(),
        "correlation_id": job.correlation_id,
        "job": {
            "job_id": job.job_id,
            "source_type": job.source_type,
            "provider": job.provider,
            "target_id": job.target_id,
            "target_url": job.target_url,
            "trigger_type": job.trigger_type,
            "request_identity_mode": job.request_identity_mode,
            "delivery_policy": job.delivery_policy,
            "correlation_id": job.correlation_id,
            "requester_id": job.requester_id,
            "requester_email": job.requester_email,
            "metadata": dict(job.metadata),
        },
        "delivery_count": 0,
        "source_event_id": source_event_id,
        "traceparent": traceparent,
        "metadata": {},
    }
    return validate_queue_message(payload)
