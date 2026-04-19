from __future__ import annotations

from typing import Any, Callable, Protocol

from .models import (
    AccessDecision,
    AssessedArtifactPackage,
    AssessmentJob,
    CorpusGroundingPackage,
    DeliveryOutcome,
    DeliveryPlan,
    ResolvedTarget,
)
from .queue import QueueMessage
from .skill_catalog import SkillCatalog


class MCPContentClient(Protocol):
    """MCPContentClient."""

    def resolve_target(
        self, target_reference: str, *, requester_context: dict[str, Any] | None = None
    ) -> ResolvedTarget: ...

    def check_user_access(
        self, target_id: str, delegated_user_context: dict[str, Any]
    ) -> AccessDecision: ...

    def get_content_by_id(
        self,
        target_id: str,
        *,
        identity_mode: str,
        include_discussion_context: bool = False,
    ) -> AssessedArtifactPackage: ...

    def get_flagged_item_context(
        self,
        target_id: str,
        *,
        identity_mode: str,
        trigger_context: dict[str, Any] | None = None,
    ) -> AssessedArtifactPackage: ...

    def resolve_page_owner(self, target_id: str) -> dict[str, Any]: ...

    def resolve_last_editor(self, target_id: str) -> dict[str, Any]: ...


class AssessmentAgent(Protocol):
    """AssessmentAgent."""

    def retrieve_corpus_grounding(
        self, artifact: AssessedArtifactPackage
    ) -> CorpusGroundingPackage: ...

    def generate_assessment(
        self,
        artifact: AssessedArtifactPackage,
        grounding: CorpusGroundingPackage,
        *,
        validation_mode: str = "hard",
    ) -> dict[str, Any]: ...

    def generate_per_control_assessment(
        self,
        artifact: AssessedArtifactPackage,
        grounding: CorpusGroundingPackage,
        *,
        progress_cb: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, Any]: ...


class DeliveryPublisher(Protocol):
    """DeliveryPublisher."""

    def post_comment(
        self,
        target_id: str,
        *,
        comment_body: str,
        identity_mode: str,
        idempotency_key: str,
    ) -> DeliveryOutcome: ...

    def send_email(
        self,
        recipients: list[str],
        *,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> DeliveryOutcome: ...


class AuditSink(Protocol):
    """AuditSink."""

    def record_stage(self, job: AssessmentJob, stage: str, payload: dict[str, Any]) -> None: ...


class OrchestratorAdapter:
    """OrchestratorAdapter."""

    def __init__(
        self,
        *,
        content_client: MCPContentClient,
        assessment_agent: AssessmentAgent,
        delivery_publisher: DeliveryPublisher,
        audit_sink: AuditSink,
        skill_catalog: SkillCatalog | None = None,
    ) -> None:
        """Run init."""
        self._content_client = content_client
        self._assessment_agent = assessment_agent
        self._delivery_publisher = delivery_publisher
        self._audit_sink = audit_sink
        self._skill_catalog = skill_catalog

    def _stage_payload(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run stage payload."""
        enriched = dict(payload)
        if self._skill_catalog is not None:
            selected_skill = self._skill_catalog.skill_for_stage(stage)
            if selected_skill:
                enriched["selected_skill"] = selected_skill
        return enriched

    def collect_grounding(
        self, job: AssessmentJob
    ) -> tuple[AssessedArtifactPackage, CorpusGroundingPackage]:
        """Run collect grounding."""
        resolved = self._content_client.resolve_target(job.target_url)
        self._audit_sink.record_stage(
            job,
            "resolved_target",
            self._stage_payload("resolved_target", {"target_id": resolved.target_id}),
        )

        if job.request_identity_mode == "delegated":
            requester_context = {
                "requester_id": job.requester_id,
                "requester_email": job.requester_email,
            }
            access_decision = self._content_client.check_user_access(
                resolved.target_id,
                requester_context,
            )
        else:
            access_decision = AccessDecision(
                granted=True,
                identity_mode="app_only",
                reason="app_only_trigger",
                audit_fields={},
            )

        self._audit_sink.record_stage(
            job,
            "access_validated",
            self._stage_payload(
                "access_validated",
                {
                    "target_id": resolved.target_id,
                    "identity_mode": access_decision.identity_mode,
                    "granted": access_decision.granted,
                    "reason": access_decision.reason,
                    "audit_fields": access_decision.audit_fields,
                },
            ),
        )

        if not access_decision.granted:
            raise PermissionError(
                f"Access denied for target {resolved.target_id}: {access_decision.reason}"
            )

        artifact = self._content_client.get_content_by_id(
            resolved.target_id,
            identity_mode=job.request_identity_mode,
            include_discussion_context=True,
        )
        artifact_metadata = dict(artifact.metadata)
        artifact_metadata["job_metadata"] = dict(job.metadata)
        requested_framework = str(job.metadata.get("requested_framework") or "").strip()
        if requested_framework:
            artifact_metadata["framework_filter_override"] = requested_framework
        artifact = AssessedArtifactPackage(
            provider=artifact.provider,
            target_id=artifact.target_id,
            canonical_url=artifact.canonical_url,
            title=artifact.title,
            content=artifact.content,
            metadata=artifact_metadata,
            owner=artifact.owner,
            last_editor=artifact.last_editor,
            discussion_context=list(artifact.discussion_context),
        )
        self._audit_sink.record_stage(
            job,
            "content_retrieved",
            self._stage_payload("content_retrieved", {"target_id": artifact.target_id}),
        )

        grounding = self._assessment_agent.retrieve_corpus_grounding(artifact)
        self._audit_sink.record_stage(
            job,
            "corpus_retrieved",
            self._stage_payload(
                "corpus_retrieved",
                {
                    "corpus_a_results": len(grounding.corpus_a_results),
                    "corpus_b_results": len(grounding.corpus_b_results),
                },
            ),
        )
        return artifact, grounding

    def run_assessment(self, job: AssessmentJob) -> dict[str, Any]:
        """Run run assessment."""
        artifact, grounding = self.collect_grounding(job)
        assessment = self._assessment_agent.generate_assessment(artifact, grounding)
        self._audit_sink.record_stage(
            job,
            "assessment_generated",
            self._stage_payload(
                "assessment_generated", {"schema_version": assessment.get("schema_version", "")}
            ),
        )
        return assessment

    def run_per_control_assessment(
        self,
        job: AssessmentJob,
        *,
        progress_cb: Callable[[int, int, str, str], None] | None = None,
    ) -> dict[str, Any]:
        """Like :meth:`run_assessment` but uses a per-control LLM loop for broader coverage."""
        artifact, grounding = self.collect_grounding(job)
        assessment = self._assessment_agent.generate_per_control_assessment(
            artifact, grounding, progress_cb=progress_cb
        )
        self._audit_sink.record_stage(
            job,
            "assessment_generated",
            self._stage_payload(
                "assessment_generated",
                {
                    "schema_version": assessment.get("schema_version", ""),
                    "assessment_strategy": "per_control",
                },
            ),
        )
        return assessment

    def run_queue_message(self, message: QueueMessage) -> dict[str, Any]:
        """Run run queue message."""
        self._audit_sink.record_stage(
            message.job,
            "queue_message_received",
            self._stage_payload(
                "queue_message_received",
                {
                    "queue_message_id": message.queue_message_id,
                    "message_type": message.message_type,
                    "delivery_count": message.delivery_count,
                },
            ),
        )
        return self.run_assessment(message.job)
