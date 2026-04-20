from __future__ import annotations
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import requests  # type: ignore[import-untyped]
from runtime.credentials import get_credential_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import query_web.endpoints.compliance as _compliance_module
import query_web.pipeline.controls as controls
import query_web.pipeline.llm_chat as llm_chat
import query_web.pipeline.rag_pipeline as rag_pipeline
import query_web.pipeline.search as _search_module
import query_web.pipeline.storage as _storage_module
from query_web.endpoints.ingestion import IngestionService as _IngestionService
from query_web.security import auth as _auth
from query_web.config import (
    _canonical_framework_name,
    _form_bool,
    _load_precedence_policy,
    load_config,
)
from query_web.constants import (
    COMPLIANCE_REPORT_SCHEMA_VERSION,
    QUERY_WEB_VERSION_SIGNATURE,
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
    list_acr_tags_via_management_api as _diagnostics_list_acr_tags_via_management_api,
    register_diagnostics_endpoints,
    resolve_acr_registry_name as _diagnostics_resolve_acr_registry_name,
)
from query_web.endpoints.home import register_home_endpoints
from query_web.endpoints.status import register_status_endpoints
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
from query_web.security.prompt_injection_guard import (
    BLOCKED_PROMPT_INJECTION_MESSAGE,
    PROMPT_INJECTION_SYSTEM_PROMPT,
    VALIDATOR_SYSTEM_PROMPT,
    assess_prompt_injection,
    evaluate_prompt_risk,
    sanitise_conversation_turn,
    sanitise_untrusted_text,
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
from query_web.pipeline.search import (
    _count_search_documents_by_filter,
    _count_search_documents_total_by_filter,
    _delete_search_documents_by_filter,
    _list_search_documents_by_filter,
)
from query_web.utils import (
    _compute_normalised_text_hash,
    _dedupe_blob_prefix,
    _extension_matches_mime as _utils_extension_matches_mime,
    _extract_dedupe_hashes,
    _is_allowed_filetype as _utils_is_allowed_filetype,
    _risk_label as _utils_risk_label,
    _sanitise_blob_name_component,
    _utc_now_iso,
)
from runtime.assessment_orchestration.azure_assessment import (
    collect_azure_grounding,
    run_azure_assessment,
)
from runtime.assessment_orchestration._framework_patterns import (
    infer_single_framework as _infer_framework_filter,
)
from runtime.assessment_orchestration.state_store import CosmosPollingStateStore

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

try:
    from azure.cosmos.exceptions import CosmosResourceNotFoundError as _CosmosResourceNotFoundError
except Exception:
    _CosmosResourceNotFoundError = Exception  # type: ignore[misc,assignment]

CosmosResourceNotFoundError: type[Exception] = _CosmosResourceNotFoundError


# Helper to count blobs with a given prefix (for dry_run in clear endpoints)
def _count_blob_prefix(prefix: str) -> dict[str, int]:
    return _storage_module._count_blob_prefix(prefix, svc=_svc)


def _is_allowed_filetype(filename: str) -> bool:
    return _utils_is_allowed_filetype(filename)


def _extension_matches_mime(filename: str, mime_type: str) -> bool:
    return _utils_extension_matches_mime(filename, mime_type)


def _risk_label(value: str) -> str:
    return _utils_risk_label(value)


logger = logging.getLogger(__name__)

_INTERNAL_ERROR_MESSAGE = "An internal error occurred."


# ---------------------------------------------------------------------------
# Compliance implementation delegation — real logic lives in compliance.py
# ---------------------------------------------------------------------------


def _generate_compliance_report_result(
    payload: Any,
    *,
    progress_cb: Any = None,
) -> dict[str, Any]:
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
    return _compliance_module.generate_azure_compliance_report_result(
        payload,
        svc=_svc,
        progress_cb=progress_cb,
    )


# Search index and blob helpers — real logic lives in pipeline/search.py and
# pipeline/storage.py; thin wrappers preserve module-level names for svc callers.


def _delete_blob_prefix(prefix: str) -> dict[str, int]:
    return _storage_module._delete_blob_prefix(prefix, svc=_svc)


# Thin delegation wrappers for compliance helpers accessed via svc or app_module in tests
def _build_compliance_scope_inputs(**kwargs: Any) -> list[str]:
    return _compliance_module._build_compliance_scope_inputs(**kwargs)


def _assess_control_finding_with_llm(*args: Any, **kwargs: Any) -> Any:
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


def _branding_ctx() -> dict[str, Any]:
    """Return template context variables shared by every page response."""
    return {
        "app_title": config.app_title,
        "static_version": _STATIC_VERSION,
    }


precedence_policy = _load_precedence_policy(
    config.precedence_policy_path,
    config.controls_framework_authority_order,
)
search_client = SearchClient(
    endpoint=config.search_endpoint,
    index_name=config.search_index_name,
    credential=credential,
)

controls_search_client = SearchClient(
    endpoint=config.search_endpoint,
    index_name=config.controls_index_name,
    credential=credential,
)

# Initialise CosmosDB client
cosmos_db = None
try:
    from azure.cosmos import CosmosClient

    cosmos_client = CosmosClient(url=config.cosmos_endpoint, credential=credential)
    cosmos_db = cosmos_client.get_database_client(config.cosmos_database_name)
    conversations_container = cosmos_db.get_container_client(config.cosmos_container_name)
except (ImportError, Exception) as exc:
    # If CosmosDB is unavailable, continue with in-memory conversation tracking
    cosmos_client = None  # type: ignore[assignment]
    conversations_container = None  # type: ignore[assignment]
    import logging

    logging.warning(f"CosmosDB unavailable: {exc}. Conversations will not be persisted.")

orchestration_state_container = None
confluence_poll_state_store = None
if cosmos_client is not None and cosmos_db is not None:
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
_ingestion_svc = _IngestionService(sys.modules[__name__])


def _get_user_id(auth_token: str, session_id: str) -> str:
    return _conversations_get_user_id(auth_token, session_id)


def _load_conversation(user_id: str, conversation_id: str) -> ConversationSession:
    return _conversations_load_conversation(user_id, conversation_id, conversations_container)


def _save_conversation(session: ConversationSession) -> None:
    _conversations_save_conversation(session, conversations_container)


def _build_feedback_context(session: ConversationSession, limit: int = 5) -> str:
    return _conversations_build_feedback_context(session, limit=limit)


def _cognitive_token() -> str:
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


def _is_authorised(auth_token: str) -> bool:
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
    return _auth.is_authorised_request(auth_token, request, config)


def _unauthorised_message(request: Request | None = None) -> str:
    return _auth.unauthorised_message(request, config)


def _target_env_name() -> str:
    # TARGET_ENV is the canonical flag in this repo; ENV is accepted as fallback.
    return (
        os.getenv("TARGET_ENV", "").strip().lower() or os.getenv("ENV", "").strip().lower() or "dev"
    )


def _diagnostics_enabled() -> bool:
    return _target_env_name() != "prod"


def _check_diagnostics_access(request: Request, auth_token: str) -> JSONResponse | None:
    return _diagnostics_check_diagnostics_access(
        request,
        auth_token,
        is_authorised_request=_is_authorised_request,
        unauthorised_message=_unauthorised_message,
    )


def _resolve_acr_registry_name(explicit_registry_name: str = "") -> str:
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
    return _search_module._embed_query(question, svc=_svc)


def _hybrid_search(
    question: str,
    retrieve_k: int,
    evidence_filter: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    return _search_module._hybrid_search(
        question, retrieve_k, evidence_filter, svc=_svc
    )


def _controls_framework_ingestion_status() -> dict[str, Any]:
    return controls._controls_framework_ingestion_status(svc=_svc)


# ---------------------------------------------------------------------------
# Controls search helpers — delegated to the controls module.
# These thin wrappers preserve the module-level names used by extracted
# modules via svc.xxx while keeping the real implementations in controls.py.
# ---------------------------------------------------------------------------


def _normalise_framework_filter(raw_value: str | None) -> str | None:
    return controls._normalise_framework_filter(raw_value, svc=_svc)


def _normalise_controls_comparison_mode(raw_value: str | None) -> str:
    return controls._normalise_controls_comparison_mode(raw_value)


def _normalise_evidence_corpus(raw_value: str) -> str | None:
    return controls._normalise_evidence_corpus(raw_value)


def _normalise_evidence_corpora(values: Iterable[str] | None) -> list[str] | None:
    return controls._normalise_evidence_corpora(values)


def _parse_evidence_corpora_csv(raw_value: str | None) -> list[str] | None:
    return controls._parse_evidence_corpora_csv(raw_value)


def _resolve_evidence_corpora(
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
    *,
    default_corpora: Iterable[str] | None = None,
) -> list[str]:
    return controls._resolve_evidence_corpora(include, exclude, default_corpora=default_corpora)


def _build_evidence_corpus_filter(selected_corpora: Iterable[str]) -> str | None:
    return controls._build_evidence_corpus_filter(selected_corpora)


def _controls_coverage_disclaimer(
    *,
    controls_debug: dict[str, Any] | None,
    comparison_detected: bool,
    comparison_mode: str,
) -> str | None:
    return controls._controls_coverage_disclaimer(
        controls_debug=controls_debug,
        comparison_detected=comparison_detected,
        comparison_mode=comparison_mode,
    )


def _prepend_disclaimer(answer: str, disclaimer: str | None) -> str:
    return controls._prepend_disclaimer(answer, disclaimer)


def _framework_authority_rank(framework_name: str) -> int:
    return controls._framework_authority_rank(framework_name, svc=_svc)


def _preferred_framework_for_question(question: str) -> str | None:
    return controls._preferred_framework_for_question(question, svc=_svc)


def _precedence_policy_summary() -> str:
    return controls._precedence_policy_summary(svc=_svc)


def _apply_framework_authority_preference(
    items: list[dict[str, Any]],
    top_k: int,
    question: str,
) -> list[dict[str, Any]]:
    return controls._apply_framework_authority_preference(
        items, top_k, question, svc=_svc
    )


def _is_cross_framework_comparison_intent(question: str) -> bool:
    return controls._is_cross_framework_comparison_intent(question)


def _select_diverse_controls(items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    return controls._select_diverse_controls(items, top_k)


def _summarise_controls_distribution(
    controls_list: list[dict[str, Any]],
    controls_timings: dict[str, float],
    *,
    preferred_framework: str | None = None,
) -> dict[str, Any]:
    return controls._summarise_controls_distribution(
        controls_list, controls_timings, preferred_framework=preferred_framework
    )


def _question_focus_terms(question: str) -> list[str]:
    return controls._question_focus_terms(question)


def _controls_query_variants(question: str) -> list[str]:
    return controls._controls_query_variants(question)


def _merge_control_candidates(
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return controls._merge_control_candidates(base_items, new_items)


def _fetch_controls(
    search_text: str,
    retrieve_k: int,
    use_semantic: bool,
    framework_filter: str | None = None,
) -> list[dict[str, Any]]:
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
    return controls.controls_search(
        question,
        retrieve_k,
        use_semantic=use_semantic,
        framework_filter_override=framework_filter_override,
        comparison_mode=comparison_mode,
        svc=_svc,
    )


def _is_temperature_unsupported_error(exc: Exception) -> bool:
    return llm_chat._is_temperature_unsupported_error(exc)


def _chat_completion(
    messages: list[dict[str, str]], deployment: str, temperature: float, timeout: int = 45
) -> str:
    return llm_chat._chat_completion(
        messages,
        deployment,
        temperature,
        svc=_svc,
        timeout=timeout,
    )


def _chat_completion_with_empty_retry(
    messages: list[dict[str, str]],
    *,
    deployment: str,
    temperature: float,
    timeout: int = 45,
) -> str:
    return llm_chat._chat_completion_with_empty_retry(
        messages,
        deployment=deployment,
        temperature=temperature,
        svc=_svc,
        timeout=timeout,
    )


def _evaluate(question: str, context: str, answer: str) -> dict[str, Any]:
    return llm_chat._evaluate(question, context, answer, svc=_svc)


def _call_validator(text: str, timeout_s: int = 15) -> dict[str, Any]:
    return llm_chat._call_validator(text, svc=_svc, timeout_s=timeout_s)


def _run_rag(
    question: str,
    retrieve_k: int,
    temperature: float,
    controls_semantic: bool,
    controls_framework: str | None = None,
    controls_comparison_mode: str = "auto-detect",
    evidence_corpora_include: list[str] | None = None,
    evidence_corpora_exclude: list[str] | None = None,
    conversation_history: list[ConversationMessage] | None = None,
    feedback_context: str = "",
) -> dict[str, Any]:
    return rag_pipeline._run_rag(
        question,
        retrieve_k,
        temperature,
        controls_semantic,
        svc=_svc,
        controls_framework=controls_framework,
        controls_comparison_mode=controls_comparison_mode,
        evidence_corpora_include=evidence_corpora_include,
        evidence_corpora_exclude=evidence_corpora_exclude,
        conversation_history=conversation_history,
        feedback_context=feedback_context,
    )


# Blob name sanitisation moved to utils.py module

# ---------------------------------------------------------------------------
# Ingestion service — delegates to IngestionService in endpoints/ingestion.py.
# Module-level wrappers are kept so that patch.object(app_module, "name")
# in tests keeps working.
# ---------------------------------------------------------------------------


def _is_corpus_upload_enabled() -> bool:
    return _ingestion_svc.is_corpus_upload_enabled()


def _is_ingestion_job_trigger_enabled() -> bool:
    return _ingestion_svc.is_ingestion_job_trigger_enabled()


def _trigger_ingestion_job() -> dict[str, Any]:
    return _ingestion_svc.trigger_ingestion_job()


def _is_indexer_running(status: Any) -> bool:
    return _ingestion_svc.is_indexer_running(status)


def _wait_for_indexer_idle(indexer_name: str, timeout_seconds: int = 900) -> bool:
    return _ingestion_svc.wait_for_indexer_idle(indexer_name, timeout_seconds=timeout_seconds)


def _reset_grounding_indexer_state() -> str:
    return _ingestion_svc.reset_grounding_indexer_state()


def _get_ingestion_job_template_container(token: str) -> dict[str, Any]:
    return _ingestion_svc.get_ingestion_job_template_container(token)


def _trigger_ingestion_job_with_args(args_override: list[str] | None) -> dict[str, Any]:
    return _ingestion_svc.trigger_ingestion_job_with_args(args_override)


# Re-export the constant so diagnostics registration and other callers keep working.
from query_web.endpoints.ingestion import REQUIRED_INGESTION_METADATA_KEYS as _REQUIRED_INGESTION_METADATA_KEYS  # noqa: E402


def _blob_has_required_ingestion_metadata(metadata: dict[str, str] | None) -> bool:
    return _ingestion_svc.blob_has_required_ingestion_metadata(metadata)


def _mark_dedupe_blobs_for_reindex(
    corpus: str, dedupe_hashes: list[str], *, user_id: str
) -> dict[str, Any]:
    return _ingestion_svc.mark_dedupe_blobs_for_reindex(corpus, dedupe_hashes, user_id=user_id)


def _latest_ingestion_job_execution() -> dict[str, Any] | None:
    return _ingestion_svc.latest_ingestion_job_execution()


def _upload_corpus_files(
    files: list[UploadFile],
    user_id: str,
    *,
    corpus: str,
    corpus_role: str,
) -> dict[str, Any]:
    return _ingestion_svc.upload_corpus_files(files, user_id, corpus=corpus, corpus_role=corpus_role)


def _upload_corpus_b_files(files: list[UploadFile], user_id: str) -> dict[str, Any]:
    return _upload_corpus_files(files, user_id, corpus="b", corpus_role="narrative_guidance")


def _upload_corpus_c_files(files: list[UploadFile], user_id: str) -> dict[str, Any]:
    return _upload_corpus_files(files, user_id, corpus="c", corpus_role="assessed_artifact")


def _upload_corpus_a_reference_files(
    files: list[UploadFile],
    user_id: str,
    *,
    framework: str,
) -> dict[str, Any]:
    return _ingestion_svc.upload_corpus_a_reference_files(files, user_id, framework=framework)


# ---------------------------------------------------------------------------
# Named alias for the module — used as the svc context object for all
# endpoint and pipeline registrations below.  This removes the implicit
# sys.modules[__name__] pattern and makes the dependency explicit.
# ---------------------------------------------------------------------------
_svc = sys.modules[__name__]

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
    svc=_svc,
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
register_compliance_endpoints(app, svc=_svc)
register_corpus_endpoints(app, svc=_svc)
register_home_endpoints(app, svc=_svc)
register_ask_endpoints(
    app,
    svc=_svc,
    ask_request_model=AskRequest,
    ask_response_model=AskResponse,
)

# Register conversations endpoints
from query_web.endpoints.conversations import register_conversations_endpoints

register_conversations_endpoints(
    app,
    conversations_container,
    _is_authorised_request,
    _unauthorised_message,
)
