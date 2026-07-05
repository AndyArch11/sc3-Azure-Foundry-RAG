"""
Azure Assessment Module.

This module provides functionality to orchestrate compliance assessments for Azure resources.
It includes functions to run assessments, collect grounding information, and interact with Azure MCP servers.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Mapping

from azure.identity import DefaultAzureCredential

from .assessment_runtime import create_search_backed_assessment_agent_from_env
from .intake import build_assessment_job_from_provider_event
from .interfaces import OrchestratorAdapter
from .mcp.azure_resource import AzureMCPServer, build_azure_target_reference
from .models import AssessedArtifactPackage, CorpusGroundingPackage
from .runtime_wiring import DefaultDeliveryPublisher, StdoutAuditSink


def run_azure_assessment(
    *,
    subscription_id: str,
    resource_group: str,
    resource_ids: list[str] | None = None,
    controls_framework: str = "NIST CSF",
    env: Mapping[str, str] | None = None,
    credential: DefaultAzureCredential | None = None,
) -> dict[str, Any]:
    """Run run azure assessment.

    Args:
        subscription_id: The Azure subscription identifier.
        resource_group: The Azure resource group name.
        resource_ids: Optional list of specific Azure resource IDs to assess.
        controls_framework: The compliance framework to assess against. Azure CLI v1 supports NIST CSF only.
        env: Optional mapping of environment variables. If None, defaults to os.environ.
        credential: Optional Azure credential for authentication. If None, defaults to DefaultAzureCredential.
    Returns:
        A dictionary containing the assessment results.
    """
    subscription_value = subscription_id.strip()
    resource_group_value = resource_group.strip()
    resource_id_values = [item.strip() for item in (resource_ids or []) if item.strip()]
    framework = controls_framework.strip()

    if not subscription_value:
        raise ValueError("subscription_id must not be empty")
    if not resource_group_value and not resource_id_values:
        raise ValueError("resource_group is required when resource_ids are not supplied")
    if not framework:
        raise ValueError("controls_framework must not be empty")

    target_reference = build_azure_target_reference(
        subscription_id=subscription_value,
        resource_group=resource_group_value,
        resource_ids=resource_id_values,
    )
    provider = AzureMCPServer(credential=credential)
    resolved_env = dict(os.environ) if env is None else dict(env)
    assessment_agent = create_search_backed_assessment_agent_from_env(resolved_env)
    adapter = OrchestratorAdapter(
        content_client=provider,
        assessment_agent=assessment_agent,
        delivery_publisher=DefaultDeliveryPublisher(),
        audit_sink=StdoutAuditSink(),
    )

    job = build_assessment_job_from_provider_event(
        {
            "event_id": str(uuid.uuid4()),
            "target_id": "azure-scope",
            "target_url": target_reference,
            "trigger_type": "api_request",
            "metadata": {
                "requested_framework": framework,
                "requested_frameworks": [framework],
                "review_scope_mode": "selected",
                "source": "api",
                "subscription_id": subscription_value,
                "resource_group": resource_group_value,
                "resource_ids": resource_id_values,
            },
        },
        provider_hint="azure",
        request_identity_mode="app_only",
        delivery_policy="inline_else_email",
    )
    return adapter.run_assessment(job)


def collect_azure_grounding(
    *,
    subscription_id: str,
    resource_group: str,
    resource_ids: list[str] | None = None,
    controls_framework: str = "NIST CSF",
    env: Mapping[str, str] | None = None,
    credential: DefaultAzureCredential | None = None,
) -> tuple[AssessedArtifactPackage, CorpusGroundingPackage]:
    """Collect Azure artifact content and corpus grounding without generating the assessment.

    Returns the enriched artifact and grounding package so callers can run a
    per-control assessment loop rather than the single-pass LLM call.

    Args:
        subscription_id: The Azure subscription identifier.
        resource_group: The Azure resource group name.
        resource_ids: Optional list of specific Azure resource IDs to assess.
        controls_framework: The compliance framework to assess against. Azure CLI v1 supports NIST CSF only.
        env: Optional mapping of environment variables. If None, defaults to os.environ.
        credential: Optional Azure credential for authentication. If None, defaults to DefaultAzureCredential.
    Returns:
        A tuple containing the enriched AssessedArtifactPackage and CorpusGroundingPackage.
    """
    subscription_value = subscription_id.strip()
    resource_group_value = resource_group.strip()
    resource_id_values = [item.strip() for item in (resource_ids or []) if item.strip()]
    framework = controls_framework.strip()

    if not subscription_value:
        raise ValueError("subscription_id must not be empty")
    if not resource_group_value and not resource_id_values:
        raise ValueError("resource_group is required when resource_ids are not supplied")
    if not framework:
        raise ValueError("controls_framework must not be empty")

    target_reference = build_azure_target_reference(
        subscription_id=subscription_value,
        resource_group=resource_group_value,
        resource_ids=resource_id_values,
    )
    provider = AzureMCPServer(credential=credential)
    resolved_env = dict(os.environ) if env is None else dict(env)
    assessment_agent = create_search_backed_assessment_agent_from_env(resolved_env)
    adapter = OrchestratorAdapter(
        content_client=provider,
        assessment_agent=assessment_agent,
        delivery_publisher=DefaultDeliveryPublisher(),
        audit_sink=StdoutAuditSink(),
    )

    job = build_assessment_job_from_provider_event(
        {
            "event_id": str(uuid.uuid4()),
            "target_id": "azure-scope",
            "target_url": target_reference,
            "trigger_type": "api_request",
            "metadata": {
                "requested_framework": framework,
                "requested_frameworks": [framework],
                "review_scope_mode": "selected",
                "source": "api",
                "subscription_id": subscription_value,
                "resource_group": resource_group_value,
                "resource_ids": resource_id_values,
            },
        },
        provider_hint="azure",
        request_identity_mode="app_only",
        delivery_policy="inline_else_email",
    )
    return adapter.collect_grounding(job)
