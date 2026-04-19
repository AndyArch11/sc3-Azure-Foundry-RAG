"""Status and configuration endpoints for diagnostic and info retrieval."""

import logging
from typing import Any

from azure.search.documents import SearchClient
from fastapi import FastAPI
from fastapi.responses import JSONResponse

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
) -> None:
    """Register status and configuration endpoints with the FastAPI app."""

    @app.get("/health")
    def health() -> JSONResponse:
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
        """Diagnostic endpoint — returns document counts and reachability for both indexes."""

        def _probe(client: SearchClient, index_name: str) -> dict[str, Any]:
            try:
                pager = client.search(search_text="*", top=1, include_total_count=True)
                results = list(pager)
                # get_count() is on the pager object, available after first iteration
                count = (
                    pager.get_count() if hasattr(pager, "get_count") else ("1+" if results else 0)
                )
                return {"reachable": True, "document_count": count}
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
