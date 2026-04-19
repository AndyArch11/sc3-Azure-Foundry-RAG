from __future__ import annotations

from typing import Any, Mapping, cast

from .models import (
    AccessDecision,
    AssessedArtifactPackage,
    AssessmentJob,
    CorpusGroundingPackage,
    DeliveryOutcome,
    DeliveryPlan,
    PersonReference,
    ResolvedTarget,
)

_ALLOWED_IDENTITY_MODES = {"app_only", "delegated"}


def _require_non_empty_string(name: str, value: object) -> str:
    """Run require non empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object) -> str:
    """Run optional string."""
    return value.strip() if isinstance(value, str) else ""


def validate_identity_mode(value: object) -> str:
    """Run validate identity mode."""
    mode = _require_non_empty_string("identity_mode", value)
    if mode not in _ALLOWED_IDENTITY_MODES:
        raise ValueError(f"identity_mode must be one of {sorted(_ALLOWED_IDENTITY_MODES)}")
    return mode


def _mapping_to_str_dict(value: object) -> dict[str, Any] | None:
    """Run mapping to str dict."""
    if not isinstance(value, Mapping):
        return None
    return {str(key): val for key, val in value.items()}


def validate_person_reference(payload: Mapping[str, Any]) -> PersonReference:
    """Run validate person reference."""
    return PersonReference(
        principal_id=_require_non_empty_string("principal_id", payload.get("principal_id")),
        display_name=_require_non_empty_string("display_name", payload.get("display_name")),
        email=_optional_string(payload.get("email")),
    )


def validate_assessment_job(payload: Mapping[str, Any]) -> AssessmentJob:
    """Run validate assessment job."""
    return AssessmentJob(
        job_id=_require_non_empty_string("job_id", payload.get("job_id")),
        source_type=_require_non_empty_string("source_type", payload.get("source_type")),
        provider=_require_non_empty_string("provider", payload.get("provider")),
        target_id=_require_non_empty_string("target_id", payload.get("target_id")),
        target_url=_require_non_empty_string("target_url", payload.get("target_url")),
        trigger_type=_require_non_empty_string("trigger_type", payload.get("trigger_type")),
        request_identity_mode=cast(
            "Any", validate_identity_mode(payload.get("request_identity_mode"))
        ),
        delivery_policy=_require_non_empty_string(
            "delivery_policy", payload.get("delivery_policy")
        ),
        correlation_id=_require_non_empty_string("correlation_id", payload.get("correlation_id")),
        requester_id=_optional_string(payload.get("requester_id")),
        requester_email=_optional_string(payload.get("requester_email")),
        metadata=dict(payload.get("metadata") or {}),
    )


def validate_resolved_target(payload: Mapping[str, Any]) -> ResolvedTarget:
    """Run validate resolved target."""
    return ResolvedTarget(
        provider=_require_non_empty_string("provider", payload.get("provider")),
        target_type=_require_non_empty_string("target_type", payload.get("target_type")),
        target_id=_require_non_empty_string("target_id", payload.get("target_id")),
        canonical_url=_require_non_empty_string("canonical_url", payload.get("canonical_url")),
        title=_require_non_empty_string("title", payload.get("title")),
        container_id=_optional_string(payload.get("container_id")),
        version=_optional_string(payload.get("version")),
        metadata=dict(payload.get("metadata") or {}),
    )


def validate_access_decision(payload: Mapping[str, Any]) -> AccessDecision:
    """Run validate access decision."""
    granted = payload.get("granted")
    if not isinstance(granted, bool):
        raise ValueError("granted must be a boolean")
    return AccessDecision(
        granted=granted,
        identity_mode=cast("Any", validate_identity_mode(payload.get("identity_mode"))),
        reason=_require_non_empty_string("reason", payload.get("reason")),
        audit_fields=dict(payload.get("audit_fields") or {}),
    )


def validate_assessed_artifact_package(payload: Mapping[str, Any]) -> AssessedArtifactPackage:
    """Run validate assessed artifact package."""
    owner_payload = payload.get("owner")
    editor_payload = payload.get("last_editor")
    return AssessedArtifactPackage(
        provider=_require_non_empty_string("provider", payload.get("provider")),
        target_id=_require_non_empty_string("target_id", payload.get("target_id")),
        canonical_url=_require_non_empty_string("canonical_url", payload.get("canonical_url")),
        title=_require_non_empty_string("title", payload.get("title")),
        content=_require_non_empty_string("content", payload.get("content")),
        metadata=dict(payload.get("metadata") or {}),
        owner=(
            validate_person_reference(owner_payload) if isinstance(owner_payload, Mapping) else None
        ),
        last_editor=(
            validate_person_reference(editor_payload)
            if isinstance(editor_payload, Mapping)
            else None
        ),
        discussion_context=list(payload.get("discussion_context") or []),
    )


def validate_corpus_grounding_package(payload: Mapping[str, Any]) -> CorpusGroundingPackage:
    """Run validate corpus grounding package."""
    return CorpusGroundingPackage(
        corpus_a_results=list(payload.get("corpus_a_results") or []),
        corpus_b_results=list(payload.get("corpus_b_results") or []),
        precedence_policy_version=_optional_string(payload.get("precedence_policy_version")),
    )


def validate_delivery_plan(payload: Mapping[str, Any]) -> DeliveryPlan:
    """Run validate delivery plan."""
    recipients = payload.get("email_recipients") or []
    if not isinstance(recipients, list):
        raise ValueError("email_recipients must be a list")
    return DeliveryPlan(
        delivery_policy=_require_non_empty_string(
            "delivery_policy", payload.get("delivery_policy")
        ),
        email_recipients=tuple(str(item).strip() for item in recipients if str(item).strip()),
        inline_target=_mapping_to_str_dict(payload.get("inline_target")),
        fallback_to_email=bool(payload.get("fallback_to_email", False)),
    )


def validate_delivery_outcome(payload: Mapping[str, Any]) -> DeliveryOutcome:
    """Run validate delivery outcome."""
    success = payload.get("success")
    if not isinstance(success, bool):
        raise ValueError("success must be a boolean")
    attempted_channels = payload.get("attempted_channels") or []
    failures = payload.get("failures") or []
    if not isinstance(attempted_channels, list) or not isinstance(failures, list):
        raise ValueError("attempted_channels and failures must be lists")
    return DeliveryOutcome(
        success=success,
        attempted_channels=tuple(str(item) for item in attempted_channels),
        failures=tuple(str(item) for item in failures),
        metadata=dict(payload.get("metadata") or {}),
    )
