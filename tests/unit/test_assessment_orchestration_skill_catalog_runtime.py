from __future__ import annotations

from pathlib import Path

from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.models import (AccessDecision, AssessedArtifactPackage,
                                                     AssessmentJob, CorpusGroundingPackage,
                                                     DeliveryOutcome, ResolvedTarget)
from runtime.assessment_orchestration.runtime_wiring import create_orchestrator_adapter_from_env
from runtime.assessment_orchestration.skill_catalog import load_skill_catalog


class _FakeContentClient:
    def resolve_target(self, target_reference: str, *, requester_context=None) -> ResolvedTarget:
        return ResolvedTarget(
            provider="confluence",
            target_type="page",
            target_id="1234",
            canonical_url=target_reference,
            title="Runtime Skill Routing Test",
        )

    def check_user_access(self, target_id: str, delegated_user_context):
        return AccessDecision(
            granted=True,
            identity_mode="delegated",
            reason="ok",
            audit_fields={"target_id": target_id},
        )

    def get_content_by_id(
        self, target_id: str, *, identity_mode: str, include_discussion_context: bool = False
    ) -> AssessedArtifactPackage:
        return AssessedArtifactPackage(
            provider="confluence",
            target_id=target_id,
            canonical_url="https://example.atlassian.net/wiki/spaces/SEC/pages/1234",
            title="Runtime Skill Routing Test",
            content="Content",
        )

    def get_flagged_item_context(self, target_id: str, *, identity_mode: str, trigger_context=None):
        raise NotImplementedError()

    def resolve_page_owner(self, target_id: str):
        return {"principal_id": "owner-1"}

    def resolve_last_editor(self, target_id: str):
        return {"principal_id": "editor-1"}


class _FakeAssessmentAgent:
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
            "executive_summary": "ok",
            "findings": [],
            "citations": [],
        }


class _FakeDeliveryPublisher:
    def post_comment(
        self, target_id: str, *, comment_body: str, identity_mode: str, idempotency_key: str
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("inline",))

    def send_email(
        self, recipients: list[str], *, subject: str, body: str, idempotency_key: str
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("email",))


class _CaptureAuditSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_stage(self, job: AssessmentJob, stage: str, payload: dict) -> None:
        self.events.append((stage, payload))


def test_runtime_skill_catalog_discovers_project_skills() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    catalog = load_skill_catalog(repo_root / ".agents" / "skills")

    assert len(catalog.skills) == 9
    assert catalog.has_skill("content-retrieval")
    assert catalog.skill_for_stage("access_validated") == "access-validation"
    assert catalog.skill_for_stage("content_retrieved") == "content-retrieval"


def test_orchestrator_stage_payloads_include_selected_skill_when_catalog_present() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    catalog = load_skill_catalog(repo_root / ".agents" / "skills")

    audit = _CaptureAuditSink()
    adapter = OrchestratorAdapter(
        content_client=_FakeContentClient(),
        assessment_agent=_FakeAssessmentAgent(),
        delivery_publisher=_FakeDeliveryPublisher(),
        audit_sink=audit,
        skill_catalog=catalog,
    )

    job = AssessmentJob(
        job_id="job-1",
        source_type="manual_request",
        provider="confluence",
        target_id="1234",
        target_url="https://example.atlassian.net/wiki/spaces/SEC/pages/1234",
        trigger_type="user_request",
        request_identity_mode="delegated",
        delivery_policy="inline_else_email",
        correlation_id="corr-1",
    )

    adapter.run_assessment(job)

    payload_by_stage = {stage: payload for stage, payload in audit.events}
    assert payload_by_stage["resolved_target"]["selected_skill"] == "content-resolution"
    assert payload_by_stage["access_validated"]["selected_skill"] == "access-validation"
    assert payload_by_stage["content_retrieved"]["selected_skill"] == "content-retrieval"
    assert payload_by_stage["corpus_retrieved"]["selected_skill"] == "corpus-retrieval"
    assert payload_by_stage["assessment_generated"]["selected_skill"] == "assessment"


def test_runtime_wiring_loads_skill_catalog_from_default_agents_path() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_API_TOKEN": "token-1",
        "CONFLUENCE_AUTH_MODE": "basic",
        "CONFLUENCE_AUTH_EMAIL": "bot@example.com",
    }

    adapter = create_orchestrator_adapter_from_env(env)

    assert isinstance(adapter, OrchestratorAdapter)
    assert adapter._skill_catalog is not None
    assert adapter._skill_catalog.has_skill("assessment")
