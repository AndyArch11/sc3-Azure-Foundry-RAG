"""Status and configuration endpoints for diagnostic and info retrieval."""

import logging
import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from runtime.search import SearchClient

logger = logging.getLogger(__name__)
_INTERNAL_ERROR_MESSAGE = "Internal server error; check logs for details."


def register_status_endpoints(
    app: FastAPI,
    config,
    search_client: SearchClient,
    controls_search_client: SearchClient,
    QUERY_WEB_VERSION_SIGNATURE: str,
    precedence_policy,
    _CONTROLS_FRAMEWORK_FILTERS,
    _CORPUS_A_FRAMEWORKS,
    _is_corpus_upload_enabled,
    _is_ingestion_job_trigger_enabled,
    COMPLIANCE_REPORT_SCHEMA_VERSION,
    resolve_query_model_capabilities: Any | None = None,
) -> None:
    """Register status and configuration endpoints with the FastAPI app.

    Args:
        app: The FastAPI application instance.
        config: The application configuration object.
        search_client: The SearchClient for the grounding index.
        controls_search_client: The SearchClient for the controls index.
        QUERY_WEB_VERSION_SIGNATURE: The version signature of the query web service.
        precedence_policy: The precedence policy object.
        _CONTROLS_FRAMEWORK_FILTERS: The controls framework filters.
        _CORPUS_A_FRAMEWORKS: The supported frameworks for Corpus A.
        _is_corpus_upload_enabled: Function to check if corpus upload is enabled.
        _is_ingestion_job_trigger_enabled: Function to check if ingestion job trigger is enabled.
        COMPLIANCE_REPORT_SCHEMA_VERSION: The version of the compliance report schema.
        resolve_query_model_capabilities: Optional callable returning query model capability metadata.
    """

    def _coerce_positive_int(value: Any) -> int | None:
        """Coerce a value to a positive integer, returning None for invalid or non-positive values.

        Args:
            value: The value to coerce.

        Returns:
            The coerced positive integer, or None if the value is invalid or non-positive.
        """
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _resolve_request_timeout_hint_seconds() -> int | None:
        """Resolve the request timeout hint in seconds from environment variables.

        Returns:
            The resolved request timeout hint in seconds, or None if not set.
        """
        for name in (
            "QUERY_WEB_ASK_TIMEOUT_S",
            "LLM_CHAT_TIMEOUT_S",
            "OLLAMA_CHAT_TIMEOUT",
        ):
            timeout_hint = _coerce_positive_int(os.getenv(name))
            if timeout_hint is not None:
                return timeout_hint
        return None

    def _runtime_hints_payload() -> dict[str, Any]:
        """Generate the runtime hints payload.

        Returns:
            A dictionary containing runtime hints.
        """
        model_capabilities: dict[str, Any] = {}
        if callable(resolve_query_model_capabilities):
            try:
                candidate = resolve_query_model_capabilities()
                if isinstance(candidate, dict):
                    model_capabilities = candidate
            except Exception:
                model_capabilities = {}

        model_context_hint = _coerce_positive_int(
            model_capabilities.get("context_window_tokens")
            or model_capabilities.get("max_position_embeddings")
            or model_capabilities.get("max_context_tokens")
        )
        model_output_cap = _coerce_positive_int(
            model_capabilities.get("max_output_tokens")
            or model_capabilities.get("max_completion_tokens")
            or model_capabilities.get("output_token_limit")
        )
        model_source = str(model_capabilities.get("source") or "model_metadata")

        configured_query_cap = max(256, int(getattr(config, "max_completion_tokens", 1400)))
        configured_evaluator_cap = max(
            128,
            int(getattr(config, "evaluator_max_completion_tokens", 800)),
        )

        if model_output_cap is not None:
            max_completion_tokens_effective = min(configured_query_cap, model_output_cap)
            evaluator_max_completion_tokens_effective = min(
                configured_evaluator_cap,
                model_output_cap,
            )
            max_completion_tokens_source = model_source
        else:
            max_completion_tokens_effective = configured_query_cap
            evaluator_max_completion_tokens_effective = configured_evaluator_cap
            max_completion_tokens_source = "runtime_config"

        if model_context_hint is not None:
            context_window_tokens_hint = model_context_hint
            context_window_source = model_source
        else:
            context_window_tokens_hint = None
            context_window_source = "unknown"

        return {
            "context_window_tokens_hint": context_window_tokens_hint,
            "context_window_source": context_window_source,
            "model_capabilities": model_capabilities.get("models", {}),
            "max_completion_tokens_effective": max_completion_tokens_effective,
            "max_completion_tokens_source": max_completion_tokens_source,
            "evaluator_max_completion_tokens_effective": (
                evaluator_max_completion_tokens_effective
            ),
            "request_timeout_seconds_hint": _resolve_request_timeout_hint_seconds(),
            "provider_constraints_note": (
                "Runtime limits vary by provider/model and deployment configuration. "
                "Treat hints as advisory."
            ),
        }

    @app.get("/health")
    def health() -> JSONResponse:
        """Health check endpoint — returns service status and configuration details.

        Returns:
            A JSONResponse containing the health status and configuration details of the service.
        """
        return JSONResponse(
            {
                "status": "ok",
                "service": "rag-query-web",
                "version_signature": QUERY_WEB_VERSION_SIGNATURE,
                "index": config.search_index_name,
                "controls_index": config.controls_index_name,
                "controls_semantic_default": config.controls_semantic_default,
                "controls_framework_authority_order": list(
                    config.controls_framework_authority_order
                ),
                "precedence_policy_path": config.precedence_policy_path,
                "precedence_policy_version": precedence_policy.version,
                "precedence_policy_order": list(precedence_policy.default_framework_order),
                "prompt_injection_guard_enabled": True,
                "prompt_injection_validator_enabled": config.prompt_injection_validator_enabled,
                "prompt_injection_validator_mode": config.prompt_injection_validator_mode,
                "prompt_injection_validator_temperature": (
                    config.prompt_injection_validator_temperature
                ),
                "auth_enabled": bool(config.auth_token),
                "entra_group_auth_enabled": bool(config.required_group_object_id),
            }
        )

    @app.get("/api/index-status")
    def index_status() -> JSONResponse:
        """Diagnostic endpoint — returns document counts and reachability for both indexes.

        Returns:
            A JSONResponse containing the reachability and document counts for the grounding and controls indexes.
        """

        def _probe(client: SearchClient, index_name: str) -> dict[str, Any]:
            try:
                results = client.search(query_text="*", top=1)
                count = len(results)
                return {"reachable": True, "document_count": f"{count}+"}
            except Exception as exc:
                logger.exception("Index probe failed for %s: %s", index_name, exc)
                return {"reachable": False, "error": "index probe failed"}

        return JSONResponse(
            {
                "grounding_index": {
                    "name": config.search_index_name,
                    **_probe(search_client, config.search_index_name),
                },
                "controls_index": {
                    "name": config.controls_index_name,
                    **_probe(controls_search_client, config.controls_index_name),
                },
            }
        )

    @app.get("/api/config")
    def api_config() -> JSONResponse:
        """Configuration endpoint — returns the current service configuration details.

        Returns:
            A JSONResponse containing the current service configuration details.
        """
        return JSONResponse(
            {
                "version_signature": QUERY_WEB_VERSION_SIGNATURE,
                "search_index_name": config.search_index_name,
                "controls_index_name": config.controls_index_name,
                "embedding_deployment": config.embedding_deployment,
                "query_deployment": config.query_deployment,
                "evaluator_deployment": config.evaluator_deployment,
                "default_top_k": config.search_top_k,
                "controls_top_k": config.controls_top_k,
                "controls_semantic_default": config.controls_semantic_default,
                "controls_semantic_configuration_name": (
                    config.controls_semantic_configuration_name
                ),
                "controls_framework_filters": list(_CONTROLS_FRAMEWORK_FILTERS.keys()),
                "controls_framework_authority_order": list(
                    config.controls_framework_authority_order
                ),
                "precedence_policy_path": config.precedence_policy_path,
                "precedence_policy_version": precedence_policy.version,
                "precedence_policy_order": list(precedence_policy.default_framework_order),
                "precedence_policy_rules_count": len(precedence_policy.rules),
                "corpus_b_upload_enabled": _is_corpus_upload_enabled(),
                "corpus_c_upload_enabled": _is_corpus_upload_enabled(),
                "ingestion_job_trigger_enabled": _is_ingestion_job_trigger_enabled(),
                "ingestion_job_name": config.ingestion_job_name,
                "corpus_a_frameworks_supported": sorted(_CORPUS_A_FRAMEWORKS.keys()),
                "prompt_injection_guard_enabled": True,
                "prompt_injection_validator_enabled": (config.prompt_injection_validator_enabled),
                "prompt_injection_validator_mode": config.prompt_injection_validator_mode,
                "prompt_injection_validator_threshold": (
                    config.prompt_injection_validator_threshold
                ),
                "prompt_injection_validator_temperature": (
                    config.prompt_injection_validator_temperature
                ),
                "compliance_report_schema_version": COMPLIANCE_REPORT_SCHEMA_VERSION,
                "default_temperature": config.default_temperature,
                "evaluator_temperature": config.evaluator_temperature,
                "evaluation_threshold": config.evaluation_threshold,
                "auth_enabled": bool(config.auth_token),
                "entra_group_auth_enabled": bool(config.required_group_object_id),
            }
        )

    @app.get("/api/provider-status")
    def provider_status() -> JSONResponse:
        """Provider/runtime discovery endpoint with pre-ask operational hints.

        Returns:
            A JSONResponse containing provider metadata and advisory runtime limits.
        """
        return JSONResponse(
            {
                "provider": str(getattr(config, "cloud_provider", "") or "unknown"),
                "query_deployment": str(getattr(config, "query_deployment", "") or ""),
                "embedding_deployment": str(getattr(config, "embedding_deployment", "") or ""),
                "runtime_hints": _runtime_hints_payload(),
            }
        )
