from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

IdentityMode = Literal["app_only", "delegated"]


@dataclass(frozen=True)
class PersonReference:
    """PersonReference."""

    principal_id: str
    display_name: str
    email: str = ""


@dataclass(frozen=True)
class AssessmentJob:
    """AssessmentJob."""

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
    """ResolvedTarget."""

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
    """AccessDecision."""

    granted: bool
    identity_mode: IdentityMode
    reason: str
    audit_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssessedArtifactPackage:
    """AssessedArtifactPackage."""

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
    """CorpusGroundingPackage."""

    corpus_a_results: list[dict[str, Any]] = field(default_factory=list)
    corpus_b_results: list[dict[str, Any]] = field(default_factory=list)
    precedence_policy_version: str = ""


@dataclass(frozen=True)
class DeliveryPlan:
    """DeliveryPlan."""

    delivery_policy: str
    email_recipients: tuple[str, ...] = ()
    inline_target: dict[str, Any] | None = None
    fallback_to_email: bool = False


@dataclass(frozen=True)
class DeliveryOutcome:
    """DeliveryOutcome."""

    success: bool
    attempted_channels: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
