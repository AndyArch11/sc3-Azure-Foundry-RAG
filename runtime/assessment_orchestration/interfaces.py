"""
Assessment Orchestration Interfaces Module.

This module defines the interfaces and protocols for the assessment orchestration system.
It includes protocols for MCP content clients, assessment agents, delivery publishers, and audit sinks.
The OrchestratorAdapter class provides a concrete implementation that orchestrates the assessment process using these interfaces, handling target resolution, access validation, content retrieval, grounding collection, assessment generation, and audit logging.
The module also supports distributed tracing through traceparent propagation and allows for skill catalog integration for stage-specific skill selection.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..trace_context import scoped_trace_context
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
    """MCPContentClient.

    This protocol defines the interface for a content client that interacts with the MCP (Managed Content Platform).
    It includes methods for resolving targets, checking user access, retrieving content, and obtaining context for flagged items.
    Implementations of this protocol should provide the necessary logic to communicate with the MCP servers and handle content retrieval and access control.

    Attributes:
        resolve_target: Method to resolve a target reference to a ResolvedTarget object.
        check_user_access: Method to check user access for a given target ID and delegated user context.
        get_content_by_id: Method to retrieve content by target ID, with options for identity mode and discussion context inclusion.
        get_flagged_item_context: Method to retrieve context for flagged items by target ID, with options for identity mode and trigger context.
        resolve_page_owner: Method to resolve the owner of a page by target ID.
        resolve_last_editor: Method to resolve the last editor of a page by target ID.
    """

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
    """AssessmentAgent.

    This protocol defines the interface for an assessment agent that performs compliance assessments on artifacts.
    It includes methods for retrieving corpus grounding and generating assessments based on the artifact and grounding information.
    Implementations of this protocol should provide the necessary logic to interact with the assessment models and generate assessments according to the specified validation modes and progress callbacks.

    Attributes:
        retrieve_corpus_grounding: Method to retrieve corpus grounding for a given assessed artifact.
        generate_assessment: Method to generate an assessment based on the assessed artifact and corpus grounding, with an optional validation mode.
        generate_per_control_assessment: Method to generate a per-control assessment based on the assessed artifact and corpus grounding, with an optional progress callback for reporting progress during the assessment process.
    """

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
    """DeliveryPublisher.

    This protocol defines the interface for a delivery publisher that handles the delivery of assessment results.
    It includes methods for posting comments and sending emails related to assessment outcomes.
    Implementations of this protocol should provide the necessary logic to interact with the delivery channels and ensure reliable delivery of assessment information.

    Attributes:
        post_comment: Method to post a comment on a target artifact.
        send_email: Method to send an email to a list of recipients.
    """

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
    """AuditSink.

    This protocol defines the interface for an audit sink that records the stages of an assessment job.
    Implementations of this protocol should provide the necessary logic to persist audit information for tracking and analysis purposes.

    Attributes:
        record_stage: Method to record a stage of an assessment job.
    """

    def record_stage(self, job: AssessmentJob, stage: str, payload: dict[str, Any]) -> None: ...


class OrchestratorAdapter:
    """OrchestratorAdapter.

    This class provides a concrete implementation of the assessment orchestration process using the defined interfaces.
    It orchestrates the assessment workflow, including target resolution, access validation, content retrieval, grounding collection, assessment generation, and audit logging.
    It supports distributed tracing through traceparent propagation and allows for skill catalog integration for stage-specific skill selection.

    Attributes:
        _content_client: An instance of MCPContentClient for interacting with the MCP.
        _assessment_agent: An instance of AssessmentAgent for performing assessments.
        _delivery_publisher: An instance of DeliveryPublisher for handling delivery of assessment results.
        _audit_sink: An instance of AuditSink for recording audit information.
        _skill_catalog: An optional instance of SkillCatalog for stage-specific skill selection.
    """

    def __init__(
        self,
        *,
        content_client: MCPContentClient,
        assessment_agent: AssessmentAgent,
        delivery_publisher: DeliveryPublisher,
        audit_sink: AuditSink,
        skill_catalog: SkillCatalog | None = None,
    ) -> None:
        """Initialise the OrchestratorAdapter.

        Args:
            content_client: An instance of MCPContentClient for interacting with the MCP.
            assessment_agent: An instance of AssessmentAgent for performing assessments.
            delivery_publisher: An instance of DeliveryPublisher for handling delivery of assessment results.
            audit_sink: An instance of AuditSink for recording audit information.
            skill_catalog: An optional instance of SkillCatalog for stage-specific skill selection.
        """
        self._content_client = content_client
        self._assessment_agent = assessment_agent
        self._delivery_publisher = delivery_publisher
        self._audit_sink = audit_sink
        self._skill_catalog = skill_catalog

    def _stage_payload(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run stage payload.

        Args:
            stage: The name of the stage being recorded.
            payload: The payload data associated with the stage.
        Returns:
            A dictionary containing the enriched payload data for the stage, including any selected skill from the skill catalog if available.
        """
        enriched = dict(payload)
        if self._skill_catalog is not None:
            selected_skill = self._skill_catalog.skill_for_stage(stage)
            if selected_skill:
                enriched["selected_skill"] = selected_skill
        return enriched

    def _job_with_traceparent(self, job: AssessmentJob, traceparent: str) -> AssessmentJob:
        """Run job with traceparent.

        Args:
            job: The original AssessmentJob object.
            traceparent: The traceparent string for distributed tracing.
        Returns:
            A new AssessmentJob object with the traceparent included in the metadata, if provided and different from the existing traceparent.
        """
        traceparent_value = str(traceparent or "").strip()
        if not traceparent_value:
            return job
        existing_traceparent = str(job.metadata.get("traceparent") or "").strip()
        if existing_traceparent == traceparent_value:
            return job
        merged_metadata = dict(job.metadata)
        merged_metadata["traceparent"] = traceparent_value
        return AssessmentJob(
            job_id=job.job_id,
            source_type=job.source_type,
            provider=job.provider,
            target_id=job.target_id,
            target_url=job.target_url,
            trigger_type=job.trigger_type,
            request_identity_mode=job.request_identity_mode,
            delivery_policy=job.delivery_policy,
            correlation_id=job.correlation_id,
            requester_id=job.requester_id,
            requester_email=job.requester_email,
            metadata=merged_metadata,
        )

    def collect_grounding(
        self, job: AssessmentJob
    ) -> tuple[AssessedArtifactPackage, CorpusGroundingPackage]:
        """Run collect grounding.

        Args:
            job: The AssessmentJob object containing the details of the assessment request.
        Returns:
            A tuple containing the enriched AssessedArtifactPackage and CorpusGroundingPackage.
        """
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
        artifact_metadata["correlation_id"] = job.correlation_id
        traceparent = str(job.metadata.get("traceparent") or "").strip()
        if traceparent:
            artifact_metadata["traceparent"] = traceparent
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

    def run_assessment(self, job: AssessmentJob, *, traceparent: str = "") -> dict[str, Any]:
        """Run run assessment.

        Args:
            job: The AssessmentJob object containing the details of the assessment request.
            traceparent: The traceparent string for distributed tracing.
        Returns:
            A dictionary containing the assessment results.
        """
        effective_job = self._job_with_traceparent(job, traceparent)
        traceparent_value = str(effective_job.metadata.get("traceparent") or "").strip()
        with scoped_trace_context(
            correlation_id=effective_job.correlation_id,
            traceparent=traceparent_value,
        ):
            artifact, grounding = self.collect_grounding(effective_job)
            assessment = self._assessment_agent.generate_assessment(artifact, grounding)
            self._audit_sink.record_stage(
                effective_job,
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
        traceparent: str = "",
    ) -> dict[str, Any]:
        """Like :meth:`run_assessment` but uses a per-control LLM loop for broader coverage.

        Args:
            job: The AssessmentJob object containing the details of the assessment request.
            progress_cb: Optional callback function for reporting progress.
            traceparent: The traceparent string for distributed tracing.
        Returns:
            A dictionary containing the assessment results.
        """
        effective_job = self._job_with_traceparent(job, traceparent)
        traceparent_value = str(effective_job.metadata.get("traceparent") or "").strip()
        with scoped_trace_context(
            correlation_id=effective_job.correlation_id,
            traceparent=traceparent_value,
        ):
            artifact, grounding = self.collect_grounding(effective_job)
            assessment = self._assessment_agent.generate_per_control_assessment(
                artifact, grounding, progress_cb=progress_cb
            )
            self._audit_sink.record_stage(
                effective_job,
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
        """Run run queue message.

        Args:
            message: The QueueMessage object containing the details of the queue message.
        Returns:
            A dictionary containing the assessment results.
        """
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
        return self.run_assessment(message.job, traceparent=message.traceparent)
