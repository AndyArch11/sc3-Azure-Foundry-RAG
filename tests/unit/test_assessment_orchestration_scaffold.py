from __future__ import annotations

from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.models import (
    AccessDecision,
    AssessedArtifactPackage,
    AssessmentJob,
    CorpusGroundingPackage,
    DeliveryOutcome,
    PersonReference,
    ResolvedTarget,
)


class FakeContentClient:
    def resolve_target(self, target_reference: str, *, requester_context=None) -> ResolvedTarget:
        return ResolvedTarget(
            provider="sharepoint",
            target_type="page",
            target_id="page-123",
            canonical_url=target_reference,
            title="Test Page",
        )

    def check_user_access(self, target_id: str, delegated_user_context):
        return AccessDecision(
            granted=True,
            identity_mode="delegated",
            reason="ok",
            audit_fields={"target_id": target_id},
        )

    def get_content_by_id(self, target_id: str, *, identity_mode: str, include_discussion_context: bool = False) -> AssessedArtifactPackage:
        return AssessedArtifactPackage(
            provider="sharepoint",
            target_id=target_id,
            canonical_url="https://example/page",
            title="Test Page",
            content="Some assessed content",
            owner=PersonReference(principal_id="owner-1", display_name="Owner"),
        )

    def get_flagged_item_context(self, target_id: str, *, identity_mode: str, trigger_context=None):
        raise NotImplementedError()

    def resolve_page_owner(self, target_id: str):
        return {"principal_id": "owner-1"}

    def resolve_last_editor(self, target_id: str):
        return {"principal_id": "editor-1"}


class FakeAssessmentAgent:
    def retrieve_corpus_grounding(self, artifact: AssessedArtifactPackage) -> CorpusGroundingPackage:
        return CorpusGroundingPackage(
            corpus_a_results=[{"requirement_id": "REQ-1"}],
            corpus_b_results=[{"source": "Guide-1"}],
            precedence_policy_version="v1",
        )

    def generate_assessment(self, artifact: AssessedArtifactPackage, grounding: CorpusGroundingPackage, *, validation_mode: str = "hard"):
        return {
            "schema_version": "v1.1",
            "executive_summary": "OK",
            "findings": [{"finding_id": "F-1"}],
            "citations": ["REQ-1"],
        }

    def generate_per_control_assessment(self, artifact: AssessedArtifactPackage, grounding: CorpusGroundingPackage, *, progress_cb=None):
        return self.generate_assessment(artifact, grounding)


class FakeDeliveryPublisher:
    def post_comment(self, target_id: str, *, comment_body: str, identity_mode: str, idempotency_key: str) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("inline",))

    def send_email(self, recipients: list[str], *, subject: str, body: str, idempotency_key: str) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("email",))


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_stage(self, job: AssessmentJob, stage: str, payload: dict) -> None:
        self.events.append((stage, payload))


def test_orchestrator_adapter_runs_resolution_retrieval_and_assessment() -> None:
    audit_sink = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=FakeContentClient(),
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit_sink,
    )
    job = AssessmentJob(
        job_id="job-1",
        source_type="manual_request",
        provider="sharepoint",
        target_id="page-123",
        target_url="https://example/page",
        trigger_type="user_request",
        request_identity_mode="delegated",
        delivery_policy="inline_else_email",
        correlation_id="corr-1",
    )

    result = adapter.run_assessment(job)

    assert result["schema_version"] == "v1.1"
    assert [stage for stage, _ in audit_sink.events] == [
        "resolved_target",
        "access_validated",
        "content_retrieved",
        "corpus_retrieved",
        "assessment_generated",
    ]
