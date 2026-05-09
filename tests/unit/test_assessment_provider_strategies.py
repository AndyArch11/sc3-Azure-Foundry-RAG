"""Unit tests for assessment provider strategy mappings."""

from __future__ import annotations

from runtime.assessment_orchestration.provider_strategies import (
    filter_controls_for_artifact,
    get_assessment_provider_strategy,
    get_assessment_task_instruction,
    resolve_aws_region_name,
)


def test_get_assessment_provider_strategy_aws() -> None:
    strategy = get_assessment_provider_strategy("aws")
    assert strategy.provider == "aws"
    assert strategy.supports_embeddings is False
    assert strategy.uses_bedrock_chat is True


def test_get_assessment_provider_strategy_local_alias() -> None:
    strategy = get_assessment_provider_strategy("dev")
    assert strategy.provider == "local"
    assert strategy.supports_embeddings is True
    assert strategy.uses_bedrock_chat is False


def test_get_assessment_task_instruction_for_azure_artifact() -> None:
    instruction = get_assessment_task_instruction("azure")
    assert "Azure resource configuration" in instruction
    assert "Azure-specific applicability rules" in instruction


def test_get_assessment_task_instruction_default_provider() -> None:
    instruction = get_assessment_task_instruction("confluence")
    assert "Confluence page" in instruction


def test_filter_controls_for_artifact_non_azure_noop() -> None:
    controls = [{"requirement_id": "X-1"}]
    metadata: dict[str, object] = {}

    retained = filter_controls_for_artifact(
        artifact_provider="confluence",
        artifact_metadata=metadata,
        controls=controls,
    )

    assert retained == controls
    assert metadata == {}


def test_filter_controls_for_artifact_azure_filters_governance_controls() -> None:
    controls = [
        {
            "requirement_id": "GV-1",
            "control_family": "Governance",
            "requirement_text": "Document governance oversight and roles and responsibilities.",
            "guidance_text": "Review governance policy annually.",
        },
        {
            "requirement_id": "PR.AC-1",
            "control_family": "Access Control",
            "requirement_text": "Enforce multifactor authentication for privileged access.",
            "guidance_text": "Configure MFA and least privilege access.",
        },
    ]
    metadata: dict[str, object] = {
        "assessment_evidence_scope": "azure_resource_configuration_and_policy_assignments"
    }

    retained = filter_controls_for_artifact(
        artifact_provider="azure",
        artifact_metadata=metadata,
        controls=controls,
    )

    assert [item["requirement_id"] for item in retained] == ["PR.AC-1"]
    assert metadata["controls_retrieved_before_applicability_filter"] == 2
    assert metadata["controls_filtered_for_applicability"] == 1
    assert metadata["controls_retained_after_applicability_filter"] == 1


def test_resolve_aws_region_name_for_aws_provider() -> None:
    region = resolve_aws_region_name("aws", env={"AWS_REGION": "ap-southeast-2"})
    assert region == "ap-southeast-2"


def test_resolve_aws_region_name_non_aws_provider_returns_none() -> None:
    assert resolve_aws_region_name("azure", env={"AWS_REGION": "us-east-1"}) is None
