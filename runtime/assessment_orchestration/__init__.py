"""
Assessment Orchestration Runtime Module.

This module provides the core functionality for orchestrating assessments, including
assessment job creation, queue message handling, schema validation, skill catalog management,
and integration with various MCP (Message Control Protocol) servers such as Azure, Confluence,
Email, and SharePoint.

It also includes utilities for control applicability classification, LLM backend creation,
and polling worker management. The module is designed to be extensible and adaptable to different
runtime environments, with stubs provided for unavailable features.
"""

from typing import Any

from .assessment_runtime import (
    AssessmentRuntimeConfig,
    SearchBackedAssessmentAgent,
    create_search_backed_assessment_agent_from_env,
)


def _run_azure_assessment_unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run Azure assessment orchestration (unavailable in this runtime)."""
    raise RuntimeError("Azure assessment orchestration is unavailable in this runtime.")


_run_azure_assessment_impl: Any = _run_azure_assessment_unavailable

try:
    from .azure_assessment import run_azure_assessment as _imported_run_azure_assessment

    _run_azure_assessment_impl = _imported_run_azure_assessment
except Exception:
    pass


from .control_applicability import (
    ControlApplicabilityMetadata,
    classify_control_applicability,
    enrich_control_with_applicability,
)
from .dev_llms import create_chat_completion_fn, create_embedding_fn, get_llm_backend
from .intake import (
    build_assessment_job_from_email_notification,
    build_assessment_job_from_provider_event,
    build_queue_message,
)
from .interfaces import AssessmentAgent, AuditSink, DeliveryPublisher, MCPContentClient


class _AzureMCPServerUnavailable:
    """Stub class for Azure MCP server (unavailable in this runtime)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise the stub Azure MCP server.

        Raises:
            RuntimeError: Always raised to indicate that the Azure MCP server is unavailable in this runtime.
        """
        raise RuntimeError("Azure MCP resource integration is unavailable in this runtime.")


def _build_azure_target_reference_unavailable(*args: Any, **kwargs: Any) -> str:
    """Stub function for building Azure target reference (unavailable in this runtime).

    Raises:
        RuntimeError: Always raised to indicate that the Azure target reference builder is unavailable in this runtime.
    """
    raise RuntimeError("Azure target reference builder is unavailable in this runtime.")


_AzureMCPServer_impl: Any = _AzureMCPServerUnavailable
_build_azure_target_reference_impl: Any = _build_azure_target_reference_unavailable

try:
    from .mcp.azure_resource import AzureMCPServer as _ImportedAzureMCPServer
    from .mcp.azure_resource import (
        build_azure_target_reference as _imported_build_azure_target_reference,
    )

    _AzureMCPServer_impl = _ImportedAzureMCPServer
    _build_azure_target_reference_impl = _imported_build_azure_target_reference
except Exception:
    pass


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


def _run_forever_unavailable(*args: Any, **kwargs: Any) -> Any:
    """Run forever (unavailable in this runtime).

    Raises:
        RuntimeError: Always raised to indicate that the polling worker is unavailable in this runtime.
    """
    raise RuntimeError("Polling worker is unavailable in this runtime.")


def _run_poll_cycle_unavailable(*args: Any, **kwargs: Any) -> Any:
    """Run poll cycle (unavailable in this runtime).

    Raises:
        RuntimeError: Always raised to indicate that the polling worker is unavailable in this runtime.
    """
    raise RuntimeError("Polling worker is unavailable in this runtime.")


_PollCycleResult_impl: Any = Any
_PollerConfig_impl: Any = Any
_run_forever_impl: Any = _run_forever_unavailable
_run_poll_cycle_impl: Any = _run_poll_cycle_unavailable

try:
    from .polling_worker import PollCycleResult as _ImportedPollCycleResult
    from .polling_worker import PollerConfig as _ImportedPollerConfig
    from .polling_worker import run_forever as _imported_run_forever
    from .polling_worker import run_poll_cycle as _imported_run_poll_cycle

    _PollCycleResult_impl = _ImportedPollCycleResult
    _PollerConfig_impl = _ImportedPollerConfig
    _run_forever_impl = _imported_run_forever
    _run_poll_cycle_impl = _imported_run_poll_cycle
except Exception:
    pass


from .queue import (
    JobRunner,
    QueueMessage,
    deserialise_queue_message,
    serialise_queue_message,
    validate_queue_message,
)


def _create_confluence_mcp_server_from_env_unavailable(*args: Any, **kwargs: Any) -> Any:
    """Create Confluence MCP server from environment (unavailable in this runtime).

    Raises:
        RuntimeError: Always raised to indicate that the Confluence MCP server is unavailable in this runtime.
    """
    raise RuntimeError("Confluence MCP server wiring is unavailable in this runtime.")


def _create_orchestrator_adapter_from_env_unavailable(*args: Any, **kwargs: Any) -> Any:
    """Create orchestrator adapter from environment (unavailable in this runtime).

    Raises:
        RuntimeError: Always raised to indicate that the orchestrator adapter is unavailable in this runtime.
    """
    raise RuntimeError("Orchestrator adapter wiring is unavailable in this runtime.")


_create_confluence_mcp_server_from_env_impl: Any = (
    _create_confluence_mcp_server_from_env_unavailable
)
_create_orchestrator_adapter_from_env_impl: Any = _create_orchestrator_adapter_from_env_unavailable

try:
    from .runtime_wiring import (
        create_confluence_mcp_server_from_env as _imported_create_confluence_mcp_server_from_env,
    )
    from .runtime_wiring import (
        create_orchestrator_adapter_from_env as _imported_create_orchestrator_adapter_from_env,
    )

    _create_confluence_mcp_server_from_env_impl = _imported_create_confluence_mcp_server_from_env
    _create_orchestrator_adapter_from_env_impl = _imported_create_orchestrator_adapter_from_env
except Exception:
    pass


run_azure_assessment = _run_azure_assessment_impl
AzureMCPServer = _AzureMCPServer_impl
build_azure_target_reference = _build_azure_target_reference_impl
PollCycleResult = _PollCycleResult_impl
PollerConfig = _PollerConfig_impl
run_forever = _run_forever_impl
run_poll_cycle = _run_poll_cycle_impl
create_confluence_mcp_server_from_env = _create_confluence_mcp_server_from_env_impl
create_orchestrator_adapter_from_env = _create_orchestrator_adapter_from_env_impl


from .schema_validation import (
    SchemaValidationError,
    assert_named_schema,
    assert_schema_value,
    load_yaml_contract,
    resolve_schema_ref,
    to_plain_data,
)
from .skill_catalog import SkillCatalog, SkillDefinition, load_skill_catalog
from .state_store import (
    CosmosPollingStateStore,
    InMemoryPollingStateStore,
    PollingState,
    PollingStateStore,
)
from .validators import (
    validate_access_decision,
    validate_assessed_artifact_package,
    validate_assessment_job,
    validate_corpus_grounding_package,
    validate_delivery_outcome,
    validate_delivery_plan,
    validate_identity_mode,
    validate_person_reference,
    validate_resolved_target,
)
from .worker import process_queue_message, process_queue_message_json, reserialise_queue_message

__all__ = [
    "AccessDecision",
    "AssessedArtifactPackage",
    "AssessmentAgent",
    "AssessmentRuntimeConfig",
    "run_azure_assessment",
    "classify_control_applicability",
    "enrich_control_with_applicability",
    "ControlApplicabilityMetadata",
    "AssessmentJob",
    "AuditSink",
    "CorpusGroundingPackage",
    "DeliveryOutcome",
    "DeliveryPlan",
    "DeliveryPublisher",
    "MCPContentClient",
    "PersonReference",
    "QueueMessage",
    "ResolvedTarget",
    "JobRunner",
    "SchemaValidationError",
    "SkillCatalog",
    "SkillDefinition",
    "assert_named_schema",
    "assert_schema_value",
    "build_assessment_job_from_email_notification",
    "build_assessment_job_from_provider_event",
    "build_queue_message",
    "deserialise_queue_message",
    "load_yaml_contract",
    "serialise_queue_message",
    "validate_queue_message",
    "validate_access_decision",
    "validate_assessed_artifact_package",
    "validate_assessment_job",
    "validate_corpus_grounding_package",
    "validate_delivery_outcome",
    "validate_delivery_plan",
    "validate_identity_mode",
    "validate_person_reference",
    "validate_resolved_target",
    "resolve_schema_ref",
    "load_skill_catalog",
    "to_plain_data",
    "process_queue_message",
    "process_queue_message_json",
    "reserialise_queue_message",
    "create_confluence_mcp_server_from_env",
    "create_orchestrator_adapter_from_env",
    "AzureMCPServer",
    "build_azure_target_reference",
    "PollCycleResult",
    "PollerConfig",
    "run_forever",
    "run_poll_cycle",
    "SearchBackedAssessmentAgent",
    "CosmosPollingStateStore",
    "InMemoryPollingStateStore",
    "PollingState",
    "PollingStateStore",
    "create_search_backed_assessment_agent_from_env",
]
