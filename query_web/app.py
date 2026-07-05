from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Protocol

from query_web.log_config import configure_logging as _configure_logging

_configure_logging("query-web")

import requests  # type: ignore[import-untyped]
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_SearchIndexClientImpl: Any
_SearchIndexerClientImpl: Any
_BlobServiceClientImpl: Any

try:
    from azure.search.documents.indexes import SearchIndexClient as _ImportedSearchIndexClient
    from azure.search.documents.indexes import SearchIndexerClient as _ImportedSearchIndexerClient
    from azure.storage.blob import BlobServiceClient as _ImportedBlobServiceClient

    _SearchIndexClientImpl = _ImportedSearchIndexClient
    _SearchIndexerClientImpl = _ImportedSearchIndexerClient
    _BlobServiceClientImpl = _ImportedBlobServiceClient
except Exception:

    class _MissingAzureSdkClient:
        """Placeholder for missing Azure SDK client classes when the Azure SDK is not installed.

        Raises a RuntimeError when instantiated, indicating that the Azure SDK is unavailable.

        This class is used to provide a clear error message when attempting to use Azure-specific
        features in a runtime where the Azure SDK packages are not installed.

        Attributes:
            None

        Raises:
            RuntimeError: Always raised when an attempt is made to instantiate this class.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Raise a RuntimeError indicating that the Azure SDK is unavailable.
            Raises:
                RuntimeError: Always raised to indicate that the Azure SDK is not installed.
            """
            raise RuntimeError(
                "Azure SDK packages are not installed in this runtime. "
                "Azure-specific features are unavailable for the current cloud provider."
            )

    _SearchIndexClientImpl = _MissingAzureSdkClient
    _SearchIndexerClientImpl = _MissingAzureSdkClient
    _BlobServiceClientImpl = _MissingAzureSdkClient

SearchIndexClient = _SearchIndexClientImpl
SearchIndexerClient = _SearchIndexerClientImpl
BlobServiceClient = _BlobServiceClientImpl

import query_web.endpoints.compliance as _compliance_module
import query_web.pipeline.controls as controls
import query_web.pipeline.llm_chat as llm_chat
import query_web.pipeline.rag_pipeline as rag_pipeline
import query_web.pipeline.search as _search_module
import query_web.pipeline.storage as _storage_module
from query_web.config import (
    _canonical_framework_name,
    _form_bool,
    _load_precedence_policy,
    load_config,
)
from query_web.constants import (
    ALLOWED_EXTENSIONS,
    COMPLIANCE_REPORT_SCHEMA_VERSION,
    QUERY_WEB_VERSION_SIGNATURE,
)
from query_web.corpus_a import (
    _CORPUS_A_FRAMEWORKS,
    _CORPUS_A_REFERENCE_UPLOAD_TARGETS,
    _CORPUS_A_SOURCE_UPLOAD_REQUIRED_FRAMEWORKS,
    _classify_corpus_a_auto_uploads,
    _normalise_corpus_a_framework_key,
    _prepare_corpus_a_reference_uploads,
    _selected_corpus_a_frameworks,
)
from query_web.endpoints.ask import register_ask_endpoints
from query_web.endpoints.compliance import register_compliance_endpoints
from query_web.endpoints.conversations import (
    ConversationMessage,
    ConversationSession,
    ResponseRating,
)
from query_web.endpoints.conversations import (
    _build_feedback_context as _conversations_build_feedback_context,
)
from query_web.endpoints.conversations import _get_user_id as _conversations_get_user_id
from query_web.endpoints.conversations import _load_conversation as _conversations_load_conversation
from query_web.endpoints.conversations import _save_conversation as _conversations_save_conversation
from query_web.endpoints.corpus import register_corpus_endpoints
from query_web.endpoints.diagnostics import (
    check_diagnostics_access as _diagnostics_check_diagnostics_access,
)
from query_web.endpoints.diagnostics import (
    list_acr_tags_via_management_api as _diagnostics_list_acr_tags_via_management_api,
)
from query_web.endpoints.diagnostics import (
    register_diagnostics_endpoints,
)
from query_web.endpoints.diagnostics import (
    resolve_acr_registry_name as _diagnostics_resolve_acr_registry_name,
)
from query_web.endpoints.home import register_home_endpoints
from query_web.endpoints.ingestion import IngestionService as _IngestionService
from query_web.endpoints.status import register_status_endpoints
from query_web.local_startup import load_local_documents_if_needed
from query_web.metrics import register_metrics_endpoint
from query_web.models import AskRequest, AskResponse
from query_web.pipeline.answer import (
    _build_retrieval_based_fallback_answer,
    _chunk_reference_label,
    _clean_markdown_whitespace,
    _ensure_visible_answer,
    _unwrap_answer,
)
from query_web.pipeline.controls import _CONTROLS_FRAMEWORK_FILTERS
from query_web.pipeline.llm_chat import (
    CYBER_PERSONA_PROMPT,
    EVALUATOR_PROMPT,
    _json_fallback_eval,
    _parse_eval,
    _parse_validator_response,
    _prompt_injection_response,
)
from query_web.pipeline.search import (
    _count_search_documents_by_filter,
    _count_search_documents_total_by_filter,
    _delete_search_documents_by_filter,
    _list_search_documents_by_filter,
)
from query_web.request_context import register_request_context_middleware
from query_web.security import auth as _auth
from query_web.security.prompt_injection_guard import (
    BLOCKED_PROMPT_INJECTION_MESSAGE,
    PROMPT_INJECTION_SYSTEM_PROMPT,
    VALIDATOR_SYSTEM_PROMPT,
    assess_prompt_injection,
    evaluate_prompt_risk,
    sanitise_conversation_turn,
    sanitise_untrusted_text,
)
from query_web.utils import (
    _compute_normalised_text_hash,
    _dedupe_blob_prefix,
)
from query_web.utils import _extension_matches_mime as _utils_extension_matches_mime
from query_web.utils import (
    _extract_dedupe_hashes,
)
from query_web.utils import _is_allowed_filetype as _utils_is_allowed_filetype
from query_web.utils import _risk_label as _utils_risk_label
from query_web.utils import (
    _sanitise_blob_name_component,
    _utc_now_iso,
)
from runtime.assessment_orchestration._framework_patterns import (
    infer_single_framework as _infer_framework_filter,
)


def _run_azure_assessment_unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Raise a RuntimeError indicating that Azure assessment orchestration is unavailable in this runtime.
    Args:
        *args: Positional arguments passed to the function.
        **kwargs: Keyword arguments passed to the function.
    Raises:
        RuntimeError: Always raised to indicate that Azure assessment orchestration is unavailable.
    """
    raise RuntimeError("Azure assessment orchestration is unavailable in this runtime.")


def _collect_azure_grounding_unavailable(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
    """Raise a RuntimeError indicating that Azure grounding collection is unavailable in this runtime.
    Args:
        *args: Positional arguments passed to the function.
        **kwargs: Keyword arguments passed to the function.
    Raises:
        RuntimeError: Always raised to indicate that Azure grounding collection is unavailable.
    """
    raise RuntimeError("Azure grounding collection is unavailable in this runtime.")


_run_azure_assessment_impl: Any = _run_azure_assessment_unavailable
_collect_azure_grounding_impl: Any = _collect_azure_grounding_unavailable

try:
    from runtime.assessment_orchestration.azure_assessment import (
        collect_azure_grounding as _imported_collect_azure_grounding,
    )
    from runtime.assessment_orchestration.azure_assessment import (
        run_azure_assessment as _imported_run_azure_assessment,
    )

    _run_azure_assessment_impl = _imported_run_azure_assessment
    _collect_azure_grounding_impl = _imported_collect_azure_grounding
except Exception:
    pass


run_azure_assessment = _run_azure_assessment_impl
collect_azure_grounding = _collect_azure_grounding_impl


from runtime.assessment_orchestration.state_store import CosmosPollingStateStore, PollingStateStore
from runtime.credentials import get_credential_provider
from runtime.provider_core import normalise_cloud_provider
from runtime.search import get_search_client

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

try:
    from azure.cosmos.exceptions import CosmosResourceNotFoundError as _CosmosResourceNotFoundError
except Exception:
    _CosmosResourceNotFoundError = Exception  # type: ignore[misc,assignment]

CosmosResourceNotFoundError: type[Exception] = _CosmosResourceNotFoundError


# Helper to count blobs with a given prefix (for dry_run in clear endpoints)
def _count_blob_prefix(prefix: str) -> dict[str, int]:
    """Count blobs under *prefix* without deleting them (dry-run support).

    Args:
        prefix: The prefix to filter blobs.

    Returns:
        A dictionary with the count of blobs under the given prefix.
    """
    return _storage_module._count_blob_prefix(prefix, svc=_svc)


def _is_allowed_filetype(filename: str) -> bool:
    """Check if the file extension of *filename* is in the allowed list.

    Args:
        filename: The name of the file to check.

    Returns:
        True if the file extension is allowed, False otherwise.
    """
    return _utils_is_allowed_filetype(filename)


def _extension_matches_mime(filename: str, mime_type: str) -> bool:
    """Check if the file extension of *filename* matches the given MIME type.

    Args:
        filename: The name of the file to check.
        mime_type: The MIME type to match against.

    Returns:
        True if the file extension matches the MIME type, False otherwise.
    """
    return _utils_extension_matches_mime(filename, mime_type)


def _risk_label(value: str) -> str:
    """Get the risk label for a given value.

    Args:
        value: The value to evaluate.

    Returns:
        The risk label associated with the value.
    """
    return _utils_risk_label(value)


logger = logging.getLogger(__name__)


class _ConversationContainer(Protocol):
    """Protocol for a conversation container that supports reading, upserting, and querying items.

    Methods:
        read_item(item: str, partition_key: str) -> dict[str, Any]: Read an item from the container.
        upsert_item(body: dict[str, Any]) -> dict[str, Any]: Upsert an item into the container.
        query_items(query: str, parameters: list[dict[str, Any]] | None = None, partition_key: str | None = None, max_item_count: int | None = None) -> Iterable[dict[str, Any]]: Query items in the container.

    Attributes:
        None
    """

    def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]: ...

    def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        partition_key: str | None = None,
        max_item_count: int | None = None,
    ) -> Iterable[dict[str, Any]]: ...


_INTERNAL_ERROR_MESSAGE = "An internal error occurred."


# ---------------------------------------------------------------------------
# Compliance implementation delegation — real logic lives in compliance.py
# ---------------------------------------------------------------------------


def _generate_compliance_report_result(
    payload: Any,
    *,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Generate a compliance report result based on the provided payload.

    Args:
        payload: The input data for generating the compliance report.
        progress_cb: Optional callback function to report progress.

    Returns:
        A dictionary containing the compliance report result.
    """
    return _compliance_module.generate_compliance_report_result(
        payload,
        svc=_svc,
        progress_cb=progress_cb,
    )


def _generate_azure_compliance_report_result(
    payload: Any,
    *,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Generate an Azure compliance report result based on the provided payload.

    Args:
        payload: The input data for generating the Azure compliance report.
        progress_cb: Optional callback function to report progress.

    Returns:
        A dictionary containing the Azure compliance report result.
    """
    return _compliance_module.generate_azure_compliance_report_result(
        payload,
        svc=_svc,
        progress_cb=progress_cb,
    )


def _generate_aws_compliance_report_result(
    payload: Any,
    *,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Generate an AWS compliance report result based on the provided payload.

    Args:
        payload: The input data for generating the AWS compliance report.
        progress_cb: Optional callback function to report progress.

    Returns:
        A dictionary containing the AWS compliance report result.
    """
    return _compliance_module.generate_aws_compliance_report_result(
        payload,
        svc=_svc,
        progress_cb=progress_cb,
    )


# Search index and blob helpers — real logic lives in pipeline/search.py and
# pipeline/storage.py; thin wrappers preserve module-level names for svc callers.


def _delete_blob_prefix(prefix: str) -> dict[str, int]:
    """Delete blobs with the specified prefix.

    Args:
        prefix: The prefix of the blobs to delete.

    Returns:
        A dictionary containing the number of deleted blobs.
    """
    return _storage_module._delete_blob_prefix(prefix, svc=_svc)


# Thin delegation wrappers for compliance helpers accessed via svc or app_module in tests
def _build_compliance_scope_inputs(**kwargs: Any) -> list[str]:
    """Build compliance scope inputs based on provided keyword arguments.

    Args:
        **kwargs: Keyword arguments for building compliance scope inputs.

    Returns:
        A list of compliance scope inputs.
    """
    return _compliance_module._build_compliance_scope_inputs(**kwargs)


def _assess_control_finding_with_llm(*args: Any, **kwargs: Any) -> Any:
    """Assess a control finding using an LLM based on provided arguments.

    Args:
        *args: Positional arguments for the assessment.
        **kwargs: Keyword arguments for the assessment.

    Returns:
        The result of the control finding assessment.
    """
    kwargs.setdefault("svc", _svc)
    return _compliance_module._assess_control_finding_with_llm(*args, **kwargs)


# Conversation models and helpers moved to conversations.py module

app = FastAPI(title="RAG Query Console")
_APP_DIR = Path(__file__).resolve().parent
_STATIC_VERSION = str(
    max(
        int((_APP_DIR / "templates" / "index.html").stat().st_mtime),
        int((_APP_DIR / "static" / "index.css").stat().st_mtime),
        int((_APP_DIR / "static" / "index.js").stat().st_mtime),
    )
)
templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")
credential = get_credential_provider().get_sdk_credential()
config = load_config()

# Mount branding directory: prefer BRANDING_STATIC_PATH override, fall back to bundled assets.
_bundled_branding_dir = _APP_DIR / "static" / "branding"
_branding_dir: Path = (
    Path(config.branding_static_path)
    if config.branding_static_path and Path(config.branding_static_path).is_dir()
    else _bundled_branding_dir
)
app.mount(
    "/static/branding",
    StaticFiles(directory=str(_branding_dir)),
    name="branding",
)
register_request_context_middleware(app)


def _branding_ctx() -> dict[str, Any]:
    """Return template context variables shared by every page response.

    Returns:
        A dictionary containing branding-related context variables for templates.
    """
    try:
        cloud_provider = normalise_cloud_provider(os.getenv("CLOUD_PROVIDER"))
    except ValueError:
        cloud_provider = "azure"
    return {
        "app_title": config.app_title,
        "static_version": _STATIC_VERSION,
        "cloud_provider": cloud_provider,
    }


precedence_policy = _load_precedence_policy(
    config.precedence_policy_path,
    config.controls_framework_authority_order,
)

search_client = get_search_client(
    endpoint=config.search_endpoint,
    index_name=config.search_index_name,
    credential=credential,
)

controls_search_client = get_search_client(
    endpoint=config.search_endpoint,
    index_name=config.controls_index_name,
    credential=credential,
)

# Load local JSONL documents if running in local/dev mode
load_local_documents_if_needed(search_client, controls_search_client)

# Initialise CosmosDB client — skipped in local mode (no endpoint configured)
cosmos_client: Any | None = None
cosmos_db: Any | None = None
conversations_container: _ConversationContainer | None
_local_state_db_path = os.environ.get("LOCAL_STATE_DB_PATH", "").strip()
_is_local_provider = False
try:
    _is_local_provider = normalise_cloud_provider(os.getenv("CLOUD_PROVIDER")) == "local"
except ValueError:
    _is_local_provider = False


def _resolve_writable_sqlite_path(*candidates: str) -> str | None:
    """Return first candidate path whose parent directory is writable.

    Args:
        *candidates: A list of candidate file paths to check.

    Returns:
        The first writable candidate path, or None if none are writable.
    """
    for candidate in candidates:
        path_text = (candidate or "").strip()
        if not path_text:
            continue
        path = Path(path_text)
        if str(path) == ":memory:":
            return str(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Validate write access up-front to avoid startup crashes later.
            with path.open("a", encoding="utf-8"):
                pass
            return str(path)
        except Exception:
            continue
    return None


if not config.cosmos_endpoint:
    runtime_out_db = str(
        Path(__file__).resolve().parent.parent / "runtime" / "out" / "local_state.db"
    )
    tmp_db = str(Path("/tmp") / "query-web" / "local_state.db")
    if _is_local_provider:
        resolved_local_state_path = _resolve_writable_sqlite_path(
            _local_state_db_path,
            runtime_out_db,
            tmp_db,
        )
    else:
        resolved_local_state_path = _resolve_writable_sqlite_path(_local_state_db_path)
    _local_state_db_path = resolved_local_state_path or ""

    if _local_state_db_path:
        from query_web.conversation_store import SqliteConversationStore

        try:
            conversations_container = SqliteConversationStore(_local_state_db_path)
            logger.info(
                "CosmosDB not configured — using SQLite conversation store at %s",
                _local_state_db_path,
            )
        except Exception as exc:
            attempted_local_state_path = _local_state_db_path
            conversations_container = None
            _local_state_db_path = ""
            logger.warning(
                "SQLite conversation store unavailable at %s (%s); running without conversation persistence.",
                attempted_local_state_path,
                exc,
            )
    else:
        conversations_container = None
        logger.info(
            "CosmosDB not configured — running without conversation persistence (local mode)."
        )
else:
    try:
        from azure.cosmos import CosmosClient

        cosmos_client = CosmosClient(url=config.cosmos_endpoint, credential=credential)
        cosmos_database = cosmos_client.get_database_client(config.cosmos_database_name)
        cosmos_db = cosmos_database
        conversations_container = cosmos_database.get_container_client(config.cosmos_container_name)
    except (ImportError, Exception) as exc:
        # If CosmosDB is unavailable, continue with in-memory conversation tracking
        cosmos_client = None
        conversations_container = None
        logger.warning(f"CosmosDB unavailable: {exc}. Conversations will not be persisted.")

    orchestration_state_container: Any | None = None
confluence_poll_state_store: PollingStateStore | None
confluence_poll_state_store = None
if _local_state_db_path and not config.cosmos_endpoint:
    try:
        from runtime.assessment_orchestration.sqlite_state_store import SqlitePollingStateStore

        confluence_poll_state_store = SqlitePollingStateStore(_local_state_db_path)
        logger.info("Using SQLite polling state store at %s", _local_state_db_path)
    except Exception as exc:
        logger.warning("SQLite polling state store unavailable: %s", exc)
elif cosmos_client is not None and cosmos_db is not None:
    try:
        orchestration_state_container = cosmos_db.get_container_client(
            config.cosmos_orchestration_container_name
        )
        confluence_poll_state_store = CosmosPollingStateStore(orchestration_state_container)
    except Exception as exc:
        logger.warning(
            "Confluence orchestration state unavailable from Cosmos container %s: %s",
            config.cosmos_orchestration_container_name,
            exc,
        )

# Ingestion service — wraps all ingestion/upload helpers extracted to endpoints/ingestion.py.
# Must be created after config, credential, search_client are ready.


class _IngestionServiceDeps:
    """Explicit dependency container for IngestionService.

    Properties are intentionally late-bound to module globals so existing
    patch.object(app_module, ...) tests continue to work while avoiding
    injecting the whole module object.

    Attributes:
        config: The configuration object for the application.
        credential: The credential object for authentication.
        requests: The requests module for making HTTP requests.
        SearchIndexerClient: The Azure SearchIndexerClient class for interacting with Azure Search.
        BlobServiceClient: The Azure BlobServiceClient class for interacting with Azure Blob Storage.
        search_client: The search client for performing search operations.
        ALLOWED_EXTENSIONS: A list of allowed file extensions for uploads.
    """

    @property
    def config(self):
        """Return the configuration object for the application."""
        return config

    @property
    def credential(self):
        """Return the credential object for authentication."""
        return credential

    @property
    def requests(self):
        """Return the requests module for making HTTP requests."""
        return requests

    @property
    def SearchIndexerClient(self):
        """Return the Azure SearchIndexerClient class for interacting with Azure Search."""
        return SearchIndexerClient

    @property
    def BlobServiceClient(self):
        """Return the Azure BlobServiceClient class for interacting with Azure Blob Storage."""
        return BlobServiceClient

    @property
    def search_client(self):
        """Return the search client for performing search operations."""
        return search_client

    @property
    def ALLOWED_EXTENSIONS(self):
        """Return a list of allowed file extensions for uploads."""
        return ALLOWED_EXTENSIONS

    @property
    def _compute_normalised_text_hash(self):
        """Return the function to compute a normalised text hash."""
        return _compute_normalised_text_hash

    @property
    def _dedupe_blob_prefix(self):
        """Return the function to deduplicate blob prefixes."""
        return _dedupe_blob_prefix

    @property
    def _sanitise_blob_name_component(self):
        """Return the function to sanitise blob name components."""
        return _sanitise_blob_name_component

    @property
    def _prepare_corpus_a_reference_uploads(self):
        """Return the function to prepare corpus A reference uploads."""
        return _prepare_corpus_a_reference_uploads

    @property
    def _CORPUS_A_FRAMEWORKS(self):
        """Return the corpus A frameworks."""
        return _CORPUS_A_FRAMEWORKS


_ingestion_svc = _IngestionService(_IngestionServiceDeps())


def _get_user_id(auth_token: str, session_id: str) -> str:
    """Return the user ID for the given authentication token and session ID.

    Args:
        auth_token: The authentication token of the user.
        session_id: The session ID of the user.

    Returns:
        The user ID associated with the provided authentication token and session ID.
    """
    return _conversations_get_user_id(auth_token, session_id)


def _load_conversation(
    user_id: str,
    conversation_id: str,
    *,
    correlation_id: str = "",
) -> ConversationSession:
    """Load a conversation session for the given user ID and conversation ID.

    Args:
        user_id: The ID of the user.
        conversation_id: The ID of the conversation to load.
        correlation_id: Optional correlation ID for tracing requests.

    Returns:
        The loaded conversation session.
    """
    return _conversations_load_conversation(
        user_id,
        conversation_id,
        conversations_container,
        correlation_id=correlation_id,
    )


def _save_conversation(session: ConversationSession, *, correlation_id: str = "") -> None:
    """Save a conversation session.

    Args:
        session: The conversation session to save.
        correlation_id: Optional correlation ID for tracing requests.
    """
    _conversations_save_conversation(
        session,
        conversations_container,
        correlation_id=correlation_id,
    )


def _build_feedback_context(session: ConversationSession, limit: int = 5) -> str:
    """Build the feedback context for a conversation session.

    Args:
        session: The conversation session.
        limit: The maximum number of feedback items to include.

    Returns:
        The feedback context as a string.
    """
    return _conversations_build_feedback_context(session, limit=limit)


def _cognitive_token() -> str:
    """Get the cognitive services token.

    Returns:
        The cognitive services token.
    """
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


def _is_authorised(auth_token: str) -> bool:
    """Check if the provided authentication token is authorised.

    Args:
        auth_token: The authentication token to check.

    Returns:
        True if the token is authorised, False otherwise.
    """
    return _auth.is_authorised(auth_token, config)


# Pure auth helpers — re-exported from security/auth so tests that import
# them from this module continue to work.
_normalise_object_id = _auth._normalise_object_id
_split_group_values = _auth._split_group_values
_decode_client_principal = _auth._decode_client_principal
_groups_from_client_principal_header = _auth._groups_from_client_principal_header
_principal_has_group_overage = _auth._principal_has_group_overage
_request_groups = _auth._request_groups
_group_auth_failure_message = _auth._group_auth_failure_message


def _is_authorised_request(auth_token: str, request: Request | None) -> bool:
    """Check if the request is authorised based on the provided authentication token.

    Args:
        auth_token: The authentication token to check.
        request: The FastAPI request object.

    Returns:
        True if the request is authorised, False otherwise.
    """
    return _auth.is_authorised_request(auth_token, request, config)


def _unauthorised_message(request: Request | None = None) -> str:
    """Get the unauthorised message for a request.

    Args:
        request: The FastAPI request object.

    Returns:
        The unauthorised message as a string.
    """
    return _auth.unauthorised_message(request, config)


def _target_env_name() -> str:
    """Get the target environment name from environment variables.

    Returns:
        The target environment name as a lowercase string. Defaults to "dev" if not set.
    """
    # TARGET_ENV is the canonical flag in this repo; ENV is accepted as fallback.
    return (
        os.getenv("TARGET_ENV", "").strip().lower() or os.getenv("ENV", "").strip().lower() or "dev"
    )


def _diagnostics_enabled() -> bool:
    """Check if diagnostics are enabled based on the target environment.

    Returns:
        True if diagnostics are enabled (not in production), False otherwise.
    """
    return _target_env_name() != "prod"


def _check_diagnostics_access(request: Request, auth_token: str) -> JSONResponse | None:
    """Check if diagnostics access is allowed for the given request and authentication token.

    Args:
        request: The FastAPI request object.
        auth_token: The authentication token to check.

    Returns:
        A JSONResponse indicating unauthorised access if not authorised, or None if access is allowed.
    """
    return _diagnostics_check_diagnostics_access(
        request,
        auth_token,
        is_authorised_request=_is_authorised_request,
        unauthorised_message=_unauthorised_message,
    )


def _resolve_acr_registry_name(explicit_registry_name: str = "") -> str:
    """Resolve the Azure Container Registry (ACR) registry name.

    Args:
        explicit_registry_name: An explicit registry name to use. If not provided, the default will be used.

    Returns:
        The resolved ACR registry name.
    """
    return _diagnostics_resolve_acr_registry_name(explicit_registry_name)


def _list_acr_tags_via_management_api(
    *,
    subscription_id: str,
    resource_group: str,
    registry_name: str,
    repository: str,
    limit: int,
) -> dict[str, Any]:
    return _diagnostics_list_acr_tags_via_management_api(
        credential=credential,
        requests_module=requests,
        subscription_id=subscription_id,
        resource_group=resource_group,
        registry_name=registry_name,
        repository=repository,
        limit=limit,
    )


def _embed_query(question: str) -> list[float]:
    """Embed a query using the search module.

    Args:
        question: The query string to embed.

    Returns:
        A list of floats representing the embedded query.
    """
    return _search_module._embed_query(question, svc=_svc)


def _hybrid_search(
    question: str,
    retrieve_k: int,
    evidence_filter: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Perform a hybrid search using the search module.

    Args:
        question: The query string to search for.
        retrieve_k: The number of results to retrieve.
        evidence_filter: An optional filter for evidence.

    Returns:
        A tuple containing a list of search results and a dictionary of scores.
    """
    return _search_module._hybrid_search(question, retrieve_k, evidence_filter, svc=_svc)


def _controls_framework_ingestion_status() -> dict[str, Any]:
    """Get the ingestion status of controls frameworks.

    Returns:
        A dictionary containing the ingestion status of controls frameworks.
    """
    return controls._controls_framework_ingestion_status(svc=_svc)


# ---------------------------------------------------------------------------
# Controls search helpers — delegated to the controls module.
# These thin wrappers preserve the module-level names used by extracted
# modules via svc.xxx while keeping the real implementations in controls.py.
# ---------------------------------------------------------------------------


def _normalise_framework_filter(raw_value: str | None) -> str | None:
    """Normalise the framework filter value.

    Args:
        raw_value: The raw framework filter value.

    Returns:
        The normalised framework filter value.
    """
    return controls._normalise_framework_filter(raw_value, svc=_svc)


def _normalise_controls_comparison_mode(raw_value: str | None) -> str:
    """Normalise the controls comparison mode value.

    Args:
        raw_value: The raw controls comparison mode value.

    Returns:
        The normalised controls comparison mode value.
    """
    return controls._normalise_controls_comparison_mode(raw_value)


def _normalise_evidence_corpus(raw_value: str) -> str | None:
    """Normalise the evidence corpus value.

    Args:
        raw_value: The raw evidence corpus value.

    Returns:
        The normalised evidence corpus value.
    """
    return controls._normalise_evidence_corpus(raw_value)


def _normalise_evidence_corpora(values: Iterable[str] | None) -> list[str] | None:
    """Normalise the evidence corpora values.

    Args:
        values: The raw evidence corpora values.

    Returns:
        The normalised evidence corpora values.
    """
    return controls._normalise_evidence_corpora(values)


def _parse_evidence_corpora_csv(raw_value: str | None) -> list[str] | None:
    """Parse a CSV string of evidence corpora into a list of strings.

    Args:
        raw_value: The raw CSV string of evidence corpora.

    Returns:
        A list of evidence corpora strings, or None if the input is None.
    """
    return controls._parse_evidence_corpora_csv(raw_value)


def _resolve_evidence_corpora(
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
    *,
    default_corpora: Iterable[str] | None = None,
) -> list[str]:
    """Resolve the final list of evidence corpora based on include and exclude lists.

    Args:
        include: An iterable of evidence corpora to include.
        exclude: An iterable of evidence corpora to exclude.
        default_corpora: An optional iterable of default evidence corpora to use if include is None.

    Returns:
        A list of resolved evidence corpora strings.
    """
    return controls._resolve_evidence_corpora(include, exclude, default_corpora=default_corpora)


def _build_evidence_corpus_filter(selected_corpora: Iterable[str]) -> str | None:
    """Build a filter string for the selected evidence corpora.

    Args:
        selected_corpora: An iterable of selected evidence corpora.

    Returns:
        A filter string for the selected evidence corpora, or None if no corpora are selected.
    """
    return controls._build_evidence_corpus_filter(selected_corpora)


def _controls_coverage_disclaimer(
    *,
    controls_debug: dict[str, Any] | None,
    comparison_detected: bool,
    comparison_mode: str,
) -> str | None:
    """Generate a disclaimer message based on controls coverage.

    Args:
        controls_debug: A dictionary containing debug information about controls, or None.
        comparison_detected: A boolean indicating if a comparison was detected.
        comparison_mode: A string representing the comparison mode.

    Returns:
        A disclaimer message string, or None if no disclaimer is needed.
    """
    return controls._controls_coverage_disclaimer(
        controls_debug=controls_debug,
        comparison_detected=comparison_detected,
        comparison_mode=comparison_mode,
    )


def _prepend_disclaimer(answer: str, disclaimer: str | None) -> str:
    """Prepend a disclaimer to the answer if provided.

    Args:
        answer: The original answer string.
        disclaimer: The disclaimer string to prepend, or None.

    Returns:
        The answer string with the disclaimer prepended if provided.
    """
    return controls._prepend_disclaimer(answer, disclaimer)


def _framework_authority_rank(framework_name: str) -> int:
    """Get the authority rank of a given framework.

    Args:
        framework_name: The name of the framework to check.

    Returns:
        An integer representing the authority rank of the framework.
    """
    return controls._framework_authority_rank(framework_name, svc=_svc)


def _preferred_framework_for_question(question: str) -> str | None:
    """Determine the preferred framework for a given question.

    Args:
        question: The question string to evaluate.

    Returns:
        The name of the preferred framework, or None if no preference is determined.
    """
    return controls._preferred_framework_for_question(question, svc=_svc)


def _preferred_framework_context_for_question(question: str) -> dict[str, Any] | None:
    """Get the preferred framework context for a given question.

    Args:
        question: The question string to evaluate.

    Returns:
        A dictionary containing the preferred framework context, or None if no preference is determined.
    """
    return controls._preferred_framework_context_for_question(question, svc=_svc)


def _precedence_policy_summary() -> str:
    """Get a summary of the precedence policy.

    Returns:
        A string summarising the precedence policy.
    """
    return controls._precedence_policy_summary(svc=_svc)


def _apply_framework_authority_preference(
    items: list[dict[str, Any]],
    top_k: int,
    question: str,
) -> list[dict[str, Any]]:
    """Apply framework authority preference to a list of items based on the question.

    Args:
        items: A list of items (dictionaries) to apply the preference to.
        top_k: The number of top items to return after applying the preference.
        question: The question string to evaluate for framework preference.

    Returns:
        A list of items after applying the framework authority preference.
    """
    return controls._apply_framework_authority_preference(items, top_k, question, svc=_svc)


def _is_cross_framework_comparison_intent(question: str) -> bool:
    """Determine if the question indicates an intent for cross-framework comparison.

    Args:
        question: The question string to evaluate.

    Returns:
        A boolean indicating if the question is intended for cross-framework comparison.
    """
    return controls._is_cross_framework_comparison_intent(question)


def _select_diverse_controls(items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Select a diverse set of controls from the provided items.

    Args:
        items: A list of control items (dictionaries) to select from.
        top_k: The number of top diverse controls to return.

    Returns:
        A list of selected diverse control items.
    """
    return controls._select_diverse_controls(items, top_k)


def _summarise_controls_distribution(
    controls_list: list[dict[str, Any]],
    controls_timings: dict[str, float],
    *,
    preferred_framework: str | None = None,
    preferred_framework_debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise the distribution of controls.

    Args:
        controls_list: A list of control items (dictionaries) to summarise.
        controls_timings: A dictionary mapping control IDs to their timings.
        preferred_framework: The preferred framework, if any.
        preferred_framework_debug: Debug information for the preferred framework, if any.

    Returns:
        A dictionary summarising the controls distribution.
    """
    return controls._summarise_controls_distribution(
        controls_list,
        controls_timings,
        preferred_framework=preferred_framework,
        preferred_framework_debug=preferred_framework_debug,
    )


def _question_focus_terms(question: str) -> list[str]:
    """Extract focus terms from the question.

    Args:
        question: The question string to extract focus terms from.

    Returns:
        A list of focus terms extracted from the question.
    """
    return controls._question_focus_terms(question)


def _controls_query_variants(question: str) -> list[str]:
    """Generate query variants for the given question.

    Args:
        question: The question string to generate query variants for.

    Returns:
        A list of query variants for the question.
    """
    return controls._controls_query_variants(question)


def _merge_control_candidates(
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two lists of control candidates, ensuring uniqueness and preserving order.

    Args:
        base_items: The base list of control items (dictionaries).
        new_items: The new list of control items (dictionaries) to merge with the base.

    Returns:
        A merged list of control items, with duplicates removed and order preserved.
    """
    return controls._merge_control_candidates(base_items, new_items)


def _fetch_controls(
    search_text: str,
    retrieve_k: int,
    use_semantic: bool,
    framework_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch controls based on the search text, retrieval count, and framework filter.

    Args:
        search_text: The text to search for in controls.
        retrieve_k: The number of controls to retrieve.
        use_semantic: Whether to use semantic search or not.
        framework_filter: An optional filter for the framework.

    Returns:
        A list of control items (dictionaries) matching the search criteria.
    """
    return controls._fetch_controls(
        search_text,
        retrieve_k,
        use_semantic,
        framework_filter=framework_filter,
        svc=_svc,
    )


def _controls_search(
    question: str,
    retrieve_k: int,
    *,
    use_semantic: bool,
    framework_filter_override: str | None = None,
    comparison_mode: str = "auto-detect",
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Perform a controls search based on the question, retrieval count, and other parameters.

    Args:
        question: The question string to search for in controls.
        retrieve_k: The number of controls to retrieve.
        use_semantic: Whether to use semantic search or not.
        framework_filter_override: An optional override for the framework filter.
        comparison_mode: The comparison mode to use (default is "auto-detect").

    Returns:
        A tuple containing a list of control items (dictionaries) and a dictionary of scores.
    """
    return controls.controls_search(
        question,
        retrieve_k,
        use_semantic=use_semantic,
        framework_filter_override=framework_filter_override,
        comparison_mode=comparison_mode,
        svc=_svc,
    )


def _is_temperature_unsupported_error(exc: Exception) -> bool:
    """Check if the provided exception is related to an unsupported temperature error.

    Args:
        exc: The exception to check.

    Returns:
        True if the exception is an unsupported temperature error, False otherwise.
    """
    return llm_chat._is_temperature_unsupported_error(exc)


def _chat_completion(
    messages: list[dict[str, str]],
    deployment: str,
    temperature: float,
    top_p: float = 1.0,
    timeout: int = 45,
    max_completion_tokens: int | None = None,
) -> str:
    """Generate a chat completion using the provided messages and parameters.

    Args:
        messages: A list of message dictionaries for the chat.
        deployment: The deployment name for the chat model.
        temperature: The temperature setting for the chat model.
        top_p: The top-p setting for the chat model (default is 1.0).
        timeout: The timeout in seconds for the chat completion (default is 45).
        max_completion_tokens: The maximum number of tokens for the completion (optional).

    Returns:
        The generated chat completion as a string."""
    return llm_chat._chat_completion(
        messages,
        deployment,
        temperature,
        top_p,
        svc=_svc,
        timeout=timeout,
        max_completion_tokens=max_completion_tokens,
    )


def _chat_completion_with_empty_retry(
    messages: list[dict[str, str]],
    *,
    deployment: str,
    temperature: float,
    top_p: float = 1.0,
    timeout: int = 45,
    max_completion_tokens: int | None = None,
) -> str:
    """Generate a chat completion with empty retry using the provided messages and parameters.

    Args:
        messages: A list of message dictionaries for the chat.
        deployment: The deployment name for the chat model.
        temperature: The temperature setting for the chat model.
        top_p: The top-p setting for the chat model (default is 1.0).
        timeout: The timeout in seconds for the chat completion (default is 45).
        max_completion_tokens: The maximum number of tokens for the completion (optional).

    Returns:
        The generated chat completion as a string."""
    return llm_chat._chat_completion_with_empty_retry(
        messages,
        deployment=deployment,
        temperature=temperature,
        top_p=top_p,
        svc=_svc,
        timeout=timeout,
        max_completion_tokens=max_completion_tokens,
    )


def _evaluate(
    question: str,
    context: str,
    answer: str,
    evaluator_max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    """Evaluate the answer to a question given the context using an LLM.

    Args:
        question: The question string to evaluate.
        context: The context string providing relevant information.
        answer: The answer string to evaluate.
        evaluator_max_completion_tokens: The maximum number of tokens for the evaluation (optional).

    Returns:
        A dictionary containing the evaluation results.
    """
    return llm_chat._evaluate(
        question,
        context,
        answer,
        svc=_svc,
        evaluator_max_completion_tokens=evaluator_max_completion_tokens,
    )


def _call_validator(text: str, timeout_s: int = 15) -> dict[str, Any]:
    """Call the validator with the provided text and timeout.

    Args:
        text: The text to validate.
        timeout_s: The timeout in seconds for the validation (default is 15).

    Returns:
        A dictionary containing the validation results.
    """
    return llm_chat._call_validator(text, svc=_svc, timeout_s=timeout_s)


def _run_rag(
    question: str,
    retrieve_k: int,
    temperature: float,
    controls_semantic: bool,
    top_p: float = 1.0,
    max_completion_tokens: int | None = None,
    evaluator_max_completion_tokens: int | None = None,
    controls_context_cap: int | None = None,
    controls_framework: str | None = None,
    controls_comparison_mode: str = "auto-detect",
    evidence_corpora_include: list[str] | None = None,
    evidence_corpora_exclude: list[str] | None = None,
    conversation_history: list[ConversationMessage] | None = None,
    feedback_context: str = "",
) -> dict[str, Any]:
    """Run the RAG (Retrieval-Augmented Generation) pipeline with the provided parameters.

    Args:
        question: The question string to process.
        retrieve_k: The number of documents to retrieve.
        temperature: The temperature setting for the generation model.
        controls_semantic: Whether to use semantic search for controls.
        top_p: The top-p setting for the generation model (default is 1.0
        max_completion_tokens: The maximum number of tokens for the completion (optional).
        evaluator_max_completion_tokens: The maximum number of tokens for the evaluation (optional).
        controls_context_cap: The maximum context size for controls (optional).
        controls_framework: The specific controls framework to use (optional).
        controls_comparison_mode: The comparison mode for controls (default is "auto-detect").
        evidence_corpora_include: List of evidence corpora to include (optional).
        evidence_corpora_exclude: List of evidence corpora to exclude (optional).
        conversation_history: List of conversation messages (optional).
        feedback_context: Feedback context string (optional).

    Returns:
        A dictionary containing the RAG pipeline results.
    """
    return rag_pipeline._run_rag(
        question=question,
        retrieve_k=retrieve_k,
        controls_context_cap=controls_context_cap,
        temperature=temperature,
        top_p=top_p,
        controls_semantic=controls_semantic,
        svc=_svc,
        controls_framework=controls_framework,
        controls_comparison_mode=controls_comparison_mode,
        evidence_corpora_include=evidence_corpora_include,
        evidence_corpora_exclude=evidence_corpora_exclude,
        conversation_history=conversation_history,
        feedback_context=feedback_context,
        max_completion_tokens=max_completion_tokens,
        evaluator_max_completion_tokens=evaluator_max_completion_tokens,
    )


# Blob name sanitisation moved to utils.py module

# ---------------------------------------------------------------------------
# Ingestion service — delegates to IngestionService in endpoints/ingestion.py.
# Module-level wrappers are kept so that patch.object(app_module, "name")
# in tests keeps working.
# ---------------------------------------------------------------------------


def _is_corpus_upload_enabled() -> bool:
    """Check if corpus upload is enabled.

    Returns:
        True if corpus upload is enabled, False otherwise.
    """
    return _ingestion_svc.is_corpus_upload_enabled()


def _is_ingestion_job_trigger_enabled() -> bool:
    """Check if ingestion job trigger is enabled.

    Returns:
        True if ingestion job trigger is enabled, False otherwise.
    """
    return _ingestion_svc.is_ingestion_job_trigger_enabled()


def _is_aws_ecs_trigger_enabled() -> bool:
    """Check if AWS ECS trigger is enabled.

    Returns:
        True if AWS ECS trigger is enabled, False otherwise.
    """
    return _ingestion_svc.is_aws_ecs_trigger_enabled()


def _get_ecs_recent_executions() -> list[dict[str, Any]]:
    """Get recent executions from AWS ECS.

    Returns:
        A list of dictionaries containing recent ECS execution details.
    """
    return _ingestion_svc.get_ecs_recent_executions()


def _trigger_ecs_controls_task(
    framework: str,
    *,
    replace_existing: bool = False,
    dry_run: bool = False,
    no_guidance: bool = False,
) -> dict[str, Any]:
    """Trigger an AWS ECS controls task for the specified framework.

    Args:
        framework: The name of the framework for which to trigger the ECS task.
        replace_existing: Whether to replace existing tasks (default is False).
        dry_run: Whether to perform a dry run without executing the task (default is False).
        no_guidance: Whether to skip guidance during the task execution (default is False).

    Returns:
        A dictionary containing the ECS controls task results.
    """
    return _ingestion_svc.trigger_ecs_controls_task(
        framework,
        replace_existing=replace_existing,
        dry_run=dry_run,
        no_guidance=no_guidance,
    )


def _trigger_ingestion_job() -> dict[str, Any]:
    """Trigger an ingestion job.

    Returns:
        A dictionary containing the ingestion job results.
    """
    return _ingestion_svc.trigger_ingestion_job()


def _is_indexer_running(status: Any) -> bool:
    """Check if the indexer is running.

    Args:
        status: The status of the indexer.

    Returns:
        True if the indexer is running, False otherwise.
    """
    return _ingestion_svc.is_indexer_running(status)


def _wait_for_indexer_idle(indexer_name: str, timeout_seconds: int = 900) -> bool:
    """Wait for the indexer to become idle.

    Args:
        indexer_name: The name of the indexer.
        timeout_seconds: The maximum time to wait in seconds (default is 900).

    Returns:
        True if the indexer becomes idle within the timeout, False otherwise.
    """
    return _ingestion_svc.wait_for_indexer_idle(indexer_name, timeout_seconds=timeout_seconds)


def _reset_grounding_indexer_state() -> str:
    """Reset the grounding indexer state.

    Returns:
        A string indicating the result of the reset operation.
    """
    return _ingestion_svc.reset_grounding_indexer_state()


def _get_ingestion_job_template_container(token: str) -> dict[str, Any]:
    """Get the ingestion job template container.

    Args:
        token: The authentication token.

    Returns:
        A dictionary containing the ingestion job template container.
    """
    return _ingestion_svc.get_ingestion_job_template_container(token)


def _trigger_ingestion_job_with_args(args_override: list[str] | None) -> dict[str, Any]:
    """Trigger an ingestion job with arguments.

    Args:
        args_override: A list of arguments to override the default ingestion job arguments.

    Returns:
        A dictionary containing the ingestion job results.
    """
    return _ingestion_svc.trigger_ingestion_job_with_args(args_override)


def _trigger_ecs_task_with_args(args: list[str]) -> dict[str, Any]:
    """Trigger an ECS task with arguments.

    Args:
        args: A list of arguments to pass to the ECS task.

    Returns:
        A dictionary containing the ECS task results.
    """
    return _ingestion_svc.trigger_ecs_task_with_args(args)


def _trigger_ingestion_task_with_args(args_override: list[str] | None) -> dict[str, Any]:
    """Trigger an ingestion task with arguments.

    Args:
        args_override: A list of arguments to override the default ingestion task arguments.

    Returns:
        A dictionary containing the ingestion task results.
    """
    return _ingestion_svc.trigger_ingestion_task_with_args(args_override)


# Re-export the constant so diagnostics registration and other callers keep working.
from query_web.endpoints.ingestion import (  # noqa: E402
    REQUIRED_INGESTION_METADATA_KEYS as _REQUIRED_INGESTION_METADATA_KEYS,
)


def _blob_has_required_ingestion_metadata(metadata: dict[str, str] | None) -> bool:
    """Check if the blob has the required ingestion metadata.

    Args:
        metadata: A dictionary containing the blob's metadata.

    Returns:
        True if the blob has the required ingestion metadata, False otherwise.
    """
    return _ingestion_svc.blob_has_required_ingestion_metadata(metadata)


def _mark_dedupe_blobs_for_reindex(
    corpus: str, dedupe_hashes: list[str], *, user_id: str
) -> dict[str, Any]:
    """Mark deduplicated blobs for reindexing.

    Args:
        corpus: The corpus name.
        dedupe_hashes: A list of deduplication hashes for the blobs to reindex.
        user_id: The ID of the user performing the operation.

    Returns:
        A dictionary containing the results of the reindexing operation.
    """
    return _ingestion_svc.mark_dedupe_blobs_for_reindex(corpus, dedupe_hashes, user_id=user_id)


def _latest_ingestion_job_execution() -> dict[str, Any] | None:
    """Get the latest ingestion job execution.

    Returns:
        A dictionary containing the latest ingestion job execution, or None if no execution is found.
    """
    return _ingestion_svc.latest_ingestion_job_execution()


def _upload_corpus_files(
    files: list[UploadFile],
    user_id: str,
    *,
    corpus: str,
    corpus_role: str,
) -> dict[str, Any]:
    """Upload corpus files to the ingestion service.

    Args:
        files: A list of UploadFile objects to upload.
        user_id: The ID of the user performing the upload.
        corpus: The name of the corpus to which the files belong.
        corpus_role: The role of the corpus (e.g., "narrative_guidance", "assessed_artifact").

    Returns:
        A dictionary containing the results of the file upload operation.
    """
    return _ingestion_svc.upload_corpus_files(
        files, user_id, corpus=corpus, corpus_role=corpus_role
    )


def _upload_corpus_b_files(files: list[UploadFile], user_id: str) -> dict[str, Any]:
    """Upload files to corpus B.

    Args:
        files: A list of UploadFile objects to upload.
        user_id: The ID of the user performing the upload.

    Returns:
        A dictionary containing the results of the file upload operation.
    """
    return _upload_corpus_files(files, user_id, corpus="b", corpus_role="narrative_guidance")


def _upload_corpus_c_files(files: list[UploadFile], user_id: str) -> dict[str, Any]:
    """Upload files to corpus C.

    Args:
        files: A list of UploadFile objects to upload.
        user_id: The ID of the user performing the upload.

    Returns:
        A dictionary containing the results of the file upload operation.
    """
    return _upload_corpus_files(files, user_id, corpus="c", corpus_role="assessed_artifact")


def _upload_corpus_a_reference_files(
    files: list[UploadFile],
    user_id: str,
    *,
    framework: str,
) -> dict[str, Any]:
    """Upload reference files to corpus A.

    Args:
        files: A list of UploadFile objects to upload.
        user_id: The ID of the user performing the upload.
        framework: The framework to which the reference files belong.

    Returns:
        A dictionary containing the results of the file upload operation.
    """
    return _ingestion_svc.upload_corpus_a_reference_files(files, user_id, framework=framework)


# ---------------------------------------------------------------------------
# Explicit service proxy used as the svc context object for endpoint and
# pipeline registrations. It resolves attributes from module globals at access
# time so patched app_module symbols in tests remain observable.
# ---------------------------------------------------------------------------


class _AppServices:
    """A proxy class for accessing application services."""

    def __getattr__(self, name: str) -> Any:
        """Get the attribute with the given name from the module globals.

        Args:
            name: The name of the attribute to retrieve.

        Returns:
            The value of the attribute from the module globals.
        Raises:
            AttributeError: If the attribute is not found in the module globals.
        """
        try:
            return globals()[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


_svc = _AppServices()

# Register diagnostics endpoints
register_diagnostics_endpoints(
    app,
    credential,
    config,
    search_client,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    _latest_ingestion_job_execution,
    _count_blob_prefix,
    _count_search_documents_total_by_filter,
    _utc_now_iso,
    _REQUIRED_INGESTION_METADATA_KEYS,
    deps={
        "config": lambda: config,
        "credential": lambda: credential,
        "requests": lambda: requests,
        "SearchIndexClient": lambda: SearchIndexClient,
        "SearchIndexerClient": lambda: SearchIndexerClient,
        "BlobServiceClient": lambda: BlobServiceClient,
        "search_client": lambda: search_client,
        "_is_corpus_upload_enabled": lambda: _is_corpus_upload_enabled,
        "_is_ingestion_job_trigger_enabled": lambda: _is_ingestion_job_trigger_enabled,
        "_latest_ingestion_job_execution": lambda: _latest_ingestion_job_execution,
        "_count_blob_prefix": lambda: _count_blob_prefix,
        "_count_search_documents_total_by_filter": lambda: _count_search_documents_total_by_filter,
        "_utc_now_iso": lambda: _utc_now_iso,
        "_is_authorised_request": lambda: _is_authorised_request,
        "_unauthorised_message": lambda: _unauthorised_message,
    },
)


# Register status endpoints
register_status_endpoints(
    app,
    config,
    search_client,
    controls_search_client,
    QUERY_WEB_VERSION_SIGNATURE,
    precedence_policy,
    _CONTROLS_FRAMEWORK_FILTERS,
    _CORPUS_A_FRAMEWORKS,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    COMPLIANCE_REPORT_SCHEMA_VERSION,
)

# Register extracted compliance and corpus endpoints.
register_compliance_endpoints(
    app,
    deps={
        "_count_search_documents_total_by_filter": lambda: _count_search_documents_total_by_filter,
        "_generate_aws_compliance_report_result": lambda: _generate_aws_compliance_report_result,
        "_generate_azure_compliance_report_result": lambda: _generate_azure_compliance_report_result,
        "_generate_compliance_report_result": lambda: _generate_compliance_report_result,
        "_INTERNAL_ERROR_MESSAGE": lambda: _INTERNAL_ERROR_MESSAGE,
        "_is_authorised_request": lambda: _is_authorised_request,
        "search_client": lambda: search_client,
        "_unauthorised_message": lambda: _unauthorised_message,
    },
)
register_corpus_endpoints(
    app,
    deps={
        "ALLOWED_EXTENSIONS": lambda: ALLOWED_EXTENSIONS,
        "_canonical_framework_name": lambda: _canonical_framework_name,
        "_classify_corpus_a_auto_uploads": lambda: _classify_corpus_a_auto_uploads,
        "config": lambda: config,
        "confluence_poll_state_store": lambda: confluence_poll_state_store,
        "_controls_framework_ingestion_status": lambda: _controls_framework_ingestion_status,
        "controls_search_client": lambda: controls_search_client,
        "_CORPUS_A_FRAMEWORKS": lambda: _CORPUS_A_FRAMEWORKS,
        "_CORPUS_A_REFERENCE_UPLOAD_TARGETS": lambda: _CORPUS_A_REFERENCE_UPLOAD_TARGETS,
        "_CORPUS_A_SOURCE_UPLOAD_REQUIRED_FRAMEWORKS": lambda: _CORPUS_A_SOURCE_UPLOAD_REQUIRED_FRAMEWORKS,
        "_count_blob_prefix": lambda: _count_blob_prefix,
        "_count_search_documents_by_filter": lambda: _count_search_documents_by_filter,
        "_count_search_documents_total_by_filter": lambda: _count_search_documents_total_by_filter,
        "credential": lambda: credential,
        "_delete_blob_prefix": lambda: _delete_blob_prefix,
        "_delete_search_documents_by_filter": lambda: _delete_search_documents_by_filter,
        "_extension_matches_mime": lambda: _extension_matches_mime,
        "_extract_dedupe_hashes": lambda: _extract_dedupe_hashes,
        "_get_user_id": lambda: _get_user_id,
        "_INTERNAL_ERROR_MESSAGE": lambda: _INTERNAL_ERROR_MESSAGE,
        "_is_allowed_filetype": lambda: _is_allowed_filetype,
        "_is_authorised_request": lambda: _is_authorised_request,
        "_is_ingestion_job_trigger_enabled": lambda: _is_ingestion_job_trigger_enabled,
        "_is_aws_ecs_trigger_enabled": lambda: _is_aws_ecs_trigger_enabled,
        "_get_ecs_recent_executions": lambda: _get_ecs_recent_executions,
        "_trigger_ecs_controls_task": lambda: _trigger_ecs_controls_task,
        "_latest_ingestion_job_execution": lambda: _latest_ingestion_job_execution,
        "_list_search_documents_by_filter": lambda: _list_search_documents_by_filter,
        "_mark_dedupe_blobs_for_reindex": lambda: _mark_dedupe_blobs_for_reindex,
        "_normalise_corpus_a_framework_key": lambda: _normalise_corpus_a_framework_key,
        "_risk_label": lambda: _risk_label,
        "search_client": lambda: search_client,
        "_selected_corpus_a_frameworks": lambda: _selected_corpus_a_frameworks,
        "_trigger_ingestion_job": lambda: _trigger_ingestion_job,
        "_trigger_ingestion_job_with_args": lambda: _trigger_ingestion_job_with_args,
        "_trigger_ecs_task_with_args": lambda: _trigger_ecs_task_with_args,
        "_trigger_ingestion_task_with_args": lambda: _trigger_ingestion_task_with_args,
        "_unauthorised_message": lambda: _unauthorised_message,
        "_upload_corpus_a_reference_files": lambda: _upload_corpus_a_reference_files,
        "_upload_corpus_b_files": lambda: _upload_corpus_b_files,
        "_upload_corpus_c_files": lambda: _upload_corpus_c_files,
    },
)
register_home_endpoints(
    app,
    templates=templates,
    config=config,
    is_authorised_request=_is_authorised_request,
    unauthorised_message=_unauthorised_message,
    branding_ctx=_branding_ctx,
)
register_ask_endpoints(
    app,
    ask_request_model=AskRequest,
    ask_response_model=AskResponse,
    templates=templates,
    config=config,
    conversation_message_cls=ConversationMessage,
    get_user_id=_get_user_id,
    form_bool=_form_bool,
    is_authorised_request=_is_authorised_request,
    unauthorised_message=_unauthorised_message,
    normalise_controls_comparison_mode=_normalise_controls_comparison_mode,
    normalise_framework_filter=_normalise_framework_filter,
    normalise_evidence_corpora=_normalise_evidence_corpora,
    load_conversation=_load_conversation,
    build_feedback_context=_build_feedback_context,
    run_rag=lambda **kwargs: _run_rag(**kwargs),
    save_conversation=_save_conversation,
    utc_now_iso=_utc_now_iso,
    branding_ctx=_branding_ctx,
    internal_error_message=_INTERNAL_ERROR_MESSAGE,
)

# Register conversations endpoints
from query_web.endpoints.conversations import register_conversations_endpoints

if config.cloud_provider != "aws":
    register_conversations_endpoints(
        app,
        conversations_container,
        _is_authorised_request,
        _unauthorised_message,
    )
else:
    logger.info(
        "Conversation endpoints are disabled on AWS deployments because they depend on Azure CosmosDB."
    )

# Prometheus metrics scrape endpoint — GET /metrics
register_metrics_endpoint(app)
