from typing import Any

from .assessment_runtime import (
    AssessmentRuntimeConfig,
    SearchBackedAssessmentAgent,
    create_search_backed_assessment_agent_from_env,
)

try:
    from .azure_assessment import run_azure_assessment
except Exception:
    def run_azure_assessment(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(
            "Azure assessment orchestration is unavailable in this runtime."
        )

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

try:
    from .mcp.azure_resource import AzureMCPServer, build_azure_target_reference
except Exception:
    class AzureMCPServer:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Azure MCP resource integration is unavailable in this runtime.")

    def build_azure_target_reference(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("Azure target reference builder is unavailable in this runtime.")

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
try:
    from .polling_worker import PollCycleResult, PollerConfig, run_forever, run_poll_cycle
except Exception:
    PollCycleResult = Any  # type: ignore[assignment]
    PollerConfig = Any  # type: ignore[assignment]

    def run_forever(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Polling worker is unavailable in this runtime.")

    def run_poll_cycle(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Polling worker is unavailable in this runtime.")

from .queue import (
    JobRunner,
    QueueMessage,
    deserialise_queue_message,
    serialise_queue_message,
    validate_queue_message,
)
try:
    from .runtime_wiring import (
        create_confluence_mcp_server_from_env,
        create_orchestrator_adapter_from_env,
    )
except Exception:
    def create_confluence_mcp_server_from_env(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Confluence MCP server wiring is unavailable in this runtime.")

    def create_orchestrator_adapter_from_env(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Orchestrator adapter wiring is unavailable in this runtime.")
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
