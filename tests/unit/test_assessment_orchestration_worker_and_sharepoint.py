from __future__ import annotations

from runtime.assessment_orchestration.intake import (
    build_assessment_job_from_provider_event,
    build_queue_message,
)
from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.mcp.sharepoint import SharePointMCPServer
from runtime.assessment_orchestration.models import (
    AssessedArtifactPackage,
    CorpusGroundingPackage,
    DeliveryOutcome,
)
from runtime.assessment_orchestration.queue import serialise_queue_message
from runtime.assessment_orchestration.worker import process_queue_message_json


class FakeAssessmentAgent:
    def retrieve_corpus_grounding(
        self, artifact: AssessedArtifactPackage
    ) -> CorpusGroundingPackage:
        return CorpusGroundingPackage(
            corpus_a_results=[{"requirement_id": "REQ-1"}], corpus_b_results=[]
        )

    def generate_assessment(
        self,
        artifact: AssessedArtifactPackage,
        grounding: CorpusGroundingPackage,
        *,
        validation_mode: str = "hard",
    ):
        return {
            "schema_version": "v1.1",
            "executive_summary": "stub",
            "findings": [{"finding_id": "F-1"}],
            "citations": ["REQ-1"],
        }


class FakeDeliveryPublisher:
    def post_comment(
        self, target_id: str, *, comment_body: str, identity_mode: str, idempotency_key: str
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("inline",))

    def send_email(
        self, recipients: list[str], *, subject: str, body: str, idempotency_key: str
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("email",))


class FakeAuditSink:
    def __init__(self) -> None:
        self.stages: list[str] = []

    def record_stage(self, job, stage: str, payload: dict) -> None:
        self.stages.append(stage)


def test_sharepoint_stub_resolve_and_get_content() -> None:
    sp = SharePointMCPServer()

    resolved = sp.resolve_target(
        "https://tenant.sharepoint.com/sites/sec/SitePages/page.aspx?id=abc-123"
    )
    artifact = sp.get_content_by_id(
        resolved.target_id, identity_mode="app_only", include_discussion_context=True
    )

    assert resolved.provider == "sharepoint"
    assert resolved.target_id
    assert artifact.provider == "sharepoint"
    assert artifact.target_id == resolved.target_id
    assert artifact.discussion_context


def test_worker_processes_queue_message_json() -> None:
    job = build_assessment_job_from_provider_event(
        {
            "event_id": "evt-1",
            "target_id": "abc-123",
            "target_url": "https://tenant.sharepoint.com/sites/sec/SitePages/page.aspx?id=abc-123",
            "trigger_type": "mention",
        },
        provider_hint="sharepoint",
    )
    message = build_queue_message(job, source_event_id="evt-1")

    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=SharePointMCPServer(),
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit,
    )

    result = process_queue_message_json(adapter, serialise_queue_message(message))

    assert result["schema_version"] == "v1.1"
    assert "queue_message_received" in audit.stages
    assert "assessment_generated" in audit.stages
