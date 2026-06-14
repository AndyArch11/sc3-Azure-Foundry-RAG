"""Provider strategies for assessment-runtime specific behaviour."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

from runtime.provider_core import CloudProvider, normalise_cloud_provider

_AZURE_TASK_INSTRUCTION = (
    "Assess the supplied Azure resource configuration and Azure Policy assignment extract for compliance against the requested framework. "
    "This evidence is posture-focused, not a full operating model or process review.\n\n"
    "Azure-specific applicability rules:\n"
    "- Do not mark process, governance, training, incident-response, or operational lifecycle controls as compliant solely from resource configuration or Azure Policy assignment evidence.\n"
    "- When a control requires procedural or organisational evidence not present in the Azure extract, use status=insufficient_evidence or status=not_applicable, and explain why.\n"
    "- Microsoft Cloud Security Benchmark mappings can partially address downstream frameworks, but they do not by themselves establish full compliance with those mapped controls.\n"
    "- Prefer concrete resource and Azure Policy evidence for technical control checks and be explicit about residual evidence gaps."
)

_DEFAULT_TASK_INSTRUCTION = "Assess the supplied Confluence page for cyber-security compliance against the most relevant controls."

_AZURE_TECHNICAL_CONTROL_RE = re.compile(
    r"\b(mfa|multi-factor|authentication|access control|least privilege|rbac|network|firewall|segment|encrypt|encryption|key management|tls|certificate|logging|monitor|alert|backup|restore|patch|vulnerab|malware|endpoint|hardening|configuration|baseline|disable|enable|restrict|private endpoint|managed identity|secret|key vault|diagnostic|defender|inventory|discover|secure transfer|immutability|retention|deny|auditifnotexists|deployifnotexists|modify)\b",
    re.IGNORECASE,
)
_AZURE_PROCESS_CONTROL_RE = re.compile(
    r"\b(policy(?!\s+assignment)|policies|procedure|procedures|governance|strategy|roadmap|roles?\s+and\s+responsibilit|training|awareness|exercise|tabletop|legal|regulatory|compliance\s+program|audit\b|vendor|supplier|third[-\s]?party|personnel|workforce|human resources|continuity|recovery plan|communication plan|approve|approval|document(?:ed|ation)?|review cadence|oversight|charter|committee|budget|insurance|procurement)\b",
    re.IGNORECASE,
)
_AZURE_GOVERNANCE_ID_RE = re.compile(r"^(GV(?:\.|-)|ID\.GV\b|AT-\d+|PM-\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class AssessmentProviderStrategy:
    """Assessment-runtime behaviour strategy for a cloud provider."""

    provider: CloudProvider
    supports_embeddings: bool
    uses_bedrock_chat: bool


_ASSESSMENT_PROVIDER_STRATEGIES: dict[CloudProvider, AssessmentProviderStrategy] = {
    "azure": AssessmentProviderStrategy(
        provider="azure",
        supports_embeddings=True,
        uses_bedrock_chat=False,
    ),
    "aws": AssessmentProviderStrategy(
        provider="aws",
        supports_embeddings=True,
        uses_bedrock_chat=True,
    ),
    "local": AssessmentProviderStrategy(
        provider="local",
        supports_embeddings=True,
        uses_bedrock_chat=False,
    ),
}


def get_assessment_provider_strategy(cloud_provider: str | None) -> AssessmentProviderStrategy:
    """Return the cloud-provider strategy used by assessment runtime."""

    provider = normalise_cloud_provider(cloud_provider)
    return _ASSESSMENT_PROVIDER_STRATEGIES[provider]


def get_assessment_task_instruction(artifact_provider: str | None) -> str:
    """Return provider-aware task instructions for assessment prompts."""

    if (artifact_provider or "").strip().lower() == "azure":
        return _AZURE_TASK_INSTRUCTION
    return _DEFAULT_TASK_INSTRUCTION


def resolve_aws_region_name(
    cloud_provider: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return AWS region when required by provider strategy, else ``None``."""

    strategy = get_assessment_provider_strategy(cloud_provider)
    if strategy.provider != "aws":
        return None
    values = dict(os.environ) if env is None else dict(env)
    region = (values.get("AWS_REGION") or "").strip()
    return region or None


def azure_control_is_likely_applicable(control: dict[str, Any]) -> bool:
    # If control has pre-computed applicability metadata, use it
    scope = str(control.get("control_applicability_scope") or "").strip()
    confidence = float(control.get("applicability_confidence") or 0.0)

    if scope:
        # Pre-classified control: exclude clearly process/governance scopes with high confidence
        if scope == "governance" and confidence >= 0.90:
            return False
        if scope == "process" and confidence >= 0.90:
            return False
        # Include all others: technical, mixed, and low-confidence classifications
        return True

    # Fallback to runtime heuristics if no pre-computed metadata
    requirement_id = str(control.get("requirement_id") or "").strip()
    if requirement_id and _AZURE_GOVERNANCE_ID_RE.search(requirement_id):
        return False

    text = "\n".join(
        str(control.get(field) or "")
        for field in ("control_family", "requirement_text", "guidance_text")
    )
    has_technical_signal = bool(_AZURE_TECHNICAL_CONTROL_RE.search(text))
    has_process_signal = bool(_AZURE_PROCESS_CONTROL_RE.search(text))
    if has_process_signal and not has_technical_signal:
        return False
    return True


def filter_controls_for_artifact(
    *,
    artifact_provider: str | None,
    artifact_metadata: dict[str, Any],
    controls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply provider-specific applicability filtering to control candidates."""

    if (artifact_provider or "").strip().lower() != "azure":
        return controls

    evidence_scope = str(artifact_metadata.get("assessment_evidence_scope") or "").strip().lower()
    if not evidence_scope.startswith("azure_resource_configuration"):
        return controls

    retained = [item for item in controls if azure_control_is_likely_applicable(item)]
    artifact_metadata["controls_retrieved_before_applicability_filter"] = len(controls)
    artifact_metadata["controls_filtered_for_applicability"] = len(controls) - len(retained)
    artifact_metadata["controls_retained_after_applicability_filter"] = len(retained)
    return retained
