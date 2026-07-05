"""
Assessment Orchestration Models Module.

This module defines data models used in the assessment orchestration process, including AssessmentJob, ResolvedTarget, AccessDecision, AssessedArtifactPackage, CorpusGroundingPackage, DeliveryPlan, and DeliveryOutcome.
These models encapsulate the necessary information for orchestrating assessments, collecting grounding data, and delivering assessment results, while also supporting auditability and traceability through metadata fields.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

IdentityMode = Literal["app_only", "delegated"]


@dataclass(frozen=True)
class PersonReference:
    """PersonReference.

    This class represents a reference to a person involved in the assessment process, such as an owner or last editor of an assessed artifact.
    It includes the person's unique identifier, display name, and optional email address.

    Attributes:
        principal_id: A unique identifier for the person (e.g., a UUID or user ID).
        display_name: The display name of the person.
        email: An optional email address for the person.
    """

    principal_id: str
    display_name: str
    email: str = ""


@dataclass(frozen=True)
class AssessmentJob:
    """AssessmentJob.

    This class represents a job for conducting an assessment of a specific target resource.
    It includes details about the job, such as the job ID, source type, provider, target information, trigger type, identity mode, delivery policy, correlation ID, requester information, and any additional metadata associated with the job.

    Attributes:
        job_id: A unique identifier for the assessment job.
        source_type: The type of source that initiated the assessment job (e.g., "provider_event", "email_notification").
        provider: The name of the provider associated with the target resource (e.g., "azure", "aws").
        target_id: A unique identifier for the target resource being assessed.
        target_url: The canonical URL of the target resource being assessed.
        trigger_type: The type of trigger that initiated the assessment job (e.g., "mention", "scheduled", "manual").
        request_identity_mode: The identity mode used for the assessment request (e.g., "app_only", "delegated").
        delivery_policy: The policy governing how the assessment results should be delivered (e.g., "inline_else_email", "email_only").
        correlation_id: A stable correlation ID for tracking the assessment job across systems.
        requester_id: An optional identifier for the requester of the assessment job.
        requester_email: An optional email address for the requester of the assessment job.
        metadata: A dictionary containing any additional metadata associated with the assessment job.
    """

    job_id: str
    source_type: str
    provider: str
    target_id: str
    target_url: str
    trigger_type: str
    request_identity_mode: IdentityMode
    delivery_policy: str
    correlation_id: str
    requester_id: str = ""
    requester_email: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedTarget:
    """ResolvedTarget.

    This class represents a resolved target resource that has been identified for assessment.
    It includes details about the target, such as the provider, target type, target ID, canonical URL, title, container ID, version, and any additional metadata associated with the target.
    Attributes:
        provider: The name of the provider associated with the target resource (e.g., "azure", "aws").
        target_type: The type of the target resource (e.g., "vm instance", "storage_account").
        target_id: A unique identifier for the target resource.
        canonical_url: The canonical URL of the target resource.
        title: The title or name of the target resource.
        container_id: An optional identifier for the container or parent resource of the target.
        version: An optional version string for the target resource.
        metadata: A dictionary containing any additional metadata associated with the resolved target.
    """

    provider: str
    target_type: str
    target_id: str
    canonical_url: str
    title: str
    container_id: str = ""
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessDecision:
    """AccessDecision.

    This class represents an access decision made for a target resource.
    It includes details about whether access was granted, the identity mode used, the reason for the decision, and any additional audit fields.
    Attributes:
        granted: A boolean indicating whether access was granted.
        identity_mode: The identity mode used for the access decision.
        reason: A string explaining the reason for the access decision.
        audit_fields: A dictionary containing any additional audit fields associated with the access decision.
    """

    granted: bool
    identity_mode: IdentityMode
    reason: str
    audit_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssessedArtifactPackage:
    """AssessedArtifactPackage.

    This class represents a package of assessed artifacts resulting from an assessment job.
    It includes details about the provider, target ID, canonical URL, title, content, metadata, owner, last editor, and any discussion context associated with the assessed artifacts.
    Attributes:
        provider: The name of the provider associated with the assessed artifacts (e.g., "azure", "aws").
        target_id: A unique identifier for the target resource associated with the assessed artifacts.
        canonical_url: The canonical URL of the target resource associated with the assessed artifacts.
        title: The title or name of the target resource associated with the assessed artifacts.
        content: The content of the assessed artifacts, typically in a structured format (e.g., JSON, XML).
        metadata: A dictionary containing any additional metadata associated with the assessed artifacts.
        owner: An optional PersonReference representing the owner of the assessed artifacts.
        last_editor: An optional PersonReference representing the last editor of the assessed artifacts.
        discussion_context: A list of dictionaries representing any discussion context associated with the assessed artifacts.
    """

    provider: str
    target_id: str
    canonical_url: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    owner: PersonReference | None = None
    last_editor: PersonReference | None = None
    discussion_context: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CorpusGroundingPackage:
    """CorpusGroundingPackage.

    This class represents a package of corpus grounding results resulting from an assessment job.
    It includes details about the provider, target ID, canonical URL, title, content, metadata, and the results of corpus A and corpus B assessments, as well as the precedence policy version used for the assessment.
    Attributes:
        provider: The name of the provider associated with the corpus grounding results (e.g., "azure", "aws").
        target_id: A unique identifier for the target resource associated with the corpus grounding results.
        canonical_url: The canonical URL of the target resource associated with the corpus grounding results.
        title: The title or name of the target resource associated with the corpus grounding results.
        content: The content of the corpus grounding results, typically in a structured format (e.g., JSON, XML).
        metadata: A dictionary containing any additional metadata associated with the corpus grounding results.
        corpus_a_results: A list of dictionaries representing the results of corpus A assessments.
        corpus_b_results: A list of dictionaries representing the results of corpus B assessments.
        precedence_policy_version: A string indicating the version of the precedence policy used for the assessment.
    """

    corpus_a_results: list[dict[str, Any]] = field(default_factory=list)
    corpus_b_results: list[dict[str, Any]] = field(default_factory=list)
    precedence_policy_version: str = ""


@dataclass(frozen=True)
class DeliveryPlan:
    """DeliveryPlan.

    This class represents a plan for delivering assessment results.
    It includes details about the delivery channels, delivery policy, email recipients, inline target content, and whether to fallback to email delivery if inline delivery fails.
    Attributes:
        channels: A tuple of strings representing the delivery channels (e.g., "email", "inline").
        delivery_policy: A string indicating the delivery policy for the assessment results (e.g., "inline_else_email", "email_only").
        email_recipients: A tuple of strings representing the email addresses of recipients for the assessment results.
        inline_target: An optional dictionary representing the inline target content for the assessment results.
        fallback_to_email: A boolean indicating whether to fallback to email delivery if inline delivery fails.
    """

    delivery_policy: str
    email_recipients: tuple[str, ...] = ()
    inline_target: dict[str, Any] | None = None
    fallback_to_email: bool = False


@dataclass(frozen=True)
class DeliveryOutcome:
    """DeliveryOutcome.

    This class represents the outcome of delivering assessment results.
    It includes details about whether the delivery was successful, the channels attempted for delivery, any failures encountered during delivery, and any additional metadata associated with the delivery outcome.
    Attributes:
        success: A boolean indicating whether the delivery was successful.
        attempted_channels: A tuple of strings representing the delivery channels that were attempted.
        failures: A tuple of strings representing any failures encountered during delivery.
        metadata: A dictionary containing any additional metadata associated with the delivery outcome.
    """

    success: bool
    attempted_channels: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
