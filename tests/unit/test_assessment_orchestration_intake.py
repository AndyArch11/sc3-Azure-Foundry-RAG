from __future__ import annotations

from urllib.parse import urlparse

from runtime.assessment_orchestration.intake import (
    build_assessment_job_from_email_notification,
    build_assessment_job_from_provider_event,
    build_queue_message,
)
from runtime.assessment_orchestration.mcp.email import EmailMCPServer


def test_build_assessment_job_from_provider_event() -> None:
    job = build_assessment_job_from_provider_event(
        {
            "event_id": "evt-1",
            "provider": "sharepoint",
            "target_id": "doc-99",
            "target_url": "https://tenant.sharepoint.com/sites/x/doc.aspx?id=99",
            "trigger_type": "mention",
            "requester_email": "owner@example.com",
        },
        provider_hint="sharepoint",
        request_identity_mode="app_only",
    )

    assert job.provider == "sharepoint"
    assert job.target_id == "doc-99"
    assert job.request_identity_mode == "app_only"


def test_build_assessment_job_from_email_notification_and_queue_message() -> None:
    email_server = EmailMCPServer()
    parsed = email_server.parse_notification_target(
        {
            "message_id": "msg-1",
            "mailbox_id": "agent-mailbox",
            "subject": "Please assess this page",
            "body": "Can you review https://example.atlassian.net/wiki/spaces/SEC/pages/1234 ?",
            "mentioner_email": "requester@example.com",
        }
    )

    job = build_assessment_job_from_email_notification(parsed)
    queue_message = build_queue_message(job, source_event_id=parsed.get("message_id", ""))

    assert parsed["provider"] == "confluence"
    assert (urlparse(parsed["target_reference"]).hostname or "").lower().endswith(".atlassian.net")
    assert job.source_type == "email_notification"
    assert queue_message.job.job_id == job.job_id
    assert queue_message.correlation_id == job.correlation_id


def test_parse_notification_target_rejects_lookalike_host_substring_bypass() -> None:
    email_server = EmailMCPServer()
    parsed = email_server.parse_notification_target(
        {
            "message_id": "msg-2",
            "mailbox_id": "agent-mailbox",
            "subject": "Please assess this page",
            "body": "Review https://evil-atlassian.net.evil.example/wiki/spaces/SEC/pages/1234",
            "mentioner_email": "requester@example.com",
        }
    )

    assert parsed["provider"] == "unknown"


def test_build_assessment_job_from_email_notification_lookalike_host_defaults_to_email_provider() -> (
    None
):
    job = build_assessment_job_from_email_notification(
        {
            "target_reference": "https://evil-sharepoint.com.evil.example/sites/x/doc.aspx?id=99",
            "message_id": "msg-3",
        }
    )

    assert job.provider == "email"


def test_email_recipient_resolution_deduplicates_and_filters() -> None:
    email_server = EmailMCPServer()
    recipients = email_server.resolve_recipients(
        ["Owner@Example.com", "owner@example.com", "", "invalid", "last.editor@example.com"]
    )

    assert recipients == ["owner@example.com", "last.editor@example.com"]
