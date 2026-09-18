"""Home page endpoint registration."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from query_web.config import _thinking_mode_presets_for_ui
from runtime.provider_core import normalise_cloud_provider


def _extract_auth_token(request: Request) -> str:
    """Best-effort auth token extraction for initial page loads.

    Args:
        request: The incoming HTTP request.

    Returns:
        The extracted authentication token, or an empty string if not found.
    """
    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        token = str(query_params.get("auth_token", "")).strip()
        if token:
            return token

    headers = getattr(request, "headers", None)
    if headers is not None:
        for header_name in ("x-access-token", "x-auth-token"):
            token = str(headers.get(header_name, "")).strip()
            if token:
                return token

        auth_header = str(headers.get("authorization", "")).strip()
        if auth_header:
            parts = auth_header.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
            return auth_header

    cookies = getattr(request, "cookies", None)
    if cookies is not None:
        token = str(cookies.get("auth_token", "")).strip()
        if token:
            return token

    return ""


def register_home_endpoints(
    app: Any,
    svc: Any | None = None,
    *,
    templates: Any | None = None,
    config: Any | None = None,
    is_authorised_request: Any | None = None,
    unauthorised_message: Any | None = None,
    branding_ctx: Any | None = None,
    resolve_query_model_capabilities: Any | None = None,
) -> None:
    """Register home page endpoints.

    Args:
        app: The FastAPI application instance.
        svc: Optional service object providing dependencies (default is None).
        templates: Optional template rendering engine (default is None).
        config: Optional configuration object (default is None).
        is_authorised_request: Optional callable to check request authorisation (default is None).
        unauthorised_message: Optional callable to generate unauthorised messages (default is None).
        branding_ctx: Optional callable to provide branding context (default is None).
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

    def _effective_completion_limits(
        resolved_config: Any,
        resolved_capabilities: dict[str, Any] | None,
    ) -> tuple[int, int, dict[str, Any]]:
        """Resolve effective query/evaluator token limits for UI defaults and max values.

        Args:
            resolved_config: The resolved configuration object.
            resolved_capabilities: The resolved capabilities dictionary.

        Returns:
            A tuple containing the effective query cap, effective eval cap, and a dictionary of additional metadata.
        """
        base_query_cap = max(256, int(getattr(resolved_config, "max_completion_tokens", 1400)))
        base_eval_cap = max(
            128,
            int(getattr(resolved_config, "evaluator_max_completion_tokens", 800)),
        )
        model_output_cap = _coerce_positive_int(
            (resolved_capabilities or {}).get("max_output_tokens")
        )
        if model_output_cap is not None:
            effective_query_cap = min(base_query_cap, model_output_cap)
            effective_eval_cap = min(base_eval_cap, model_output_cap)
            source = str((resolved_capabilities or {}).get("source") or "model_metadata")
        else:
            effective_query_cap = base_query_cap
            effective_eval_cap = base_eval_cap
            source = "runtime_config"
        context_hint = _coerce_positive_int(
            (resolved_capabilities or {}).get("context_window_tokens")
        )
        return (
            effective_query_cap,
            effective_eval_cap,
            {
                "context_window_tokens_hint": context_hint,
                "context_window_source": str(
                    (resolved_capabilities or {}).get("source") or "unknown"
                ),
                "max_completion_tokens_effective": effective_query_cap,
                "max_completion_tokens_source": source,
                "evaluator_max_completion_tokens_effective": effective_eval_cap,
            },
        )

    def _query_model_display(resolved_config: Any) -> str:
        """Determine the display name for the query model based on the cloud provider and configuration.

        Args:
            resolved_config: The resolved configuration object.

        Returns:
            The display name for the query model.
        """
        try:
            provider = normalise_cloud_provider(os.getenv("CLOUD_PROVIDER"))
        except ValueError:
            provider = "azure"
        if provider == "local":
            return os.getenv("OLLAMA_MODEL", "llama3").strip() or "llama3"
        return str(getattr(resolved_config, "query_deployment", "")).strip()

    def _dep(name: str, value: Any) -> Any:
        """Resolve a dependency value, falling back to the service object if not provided.

        Args:
            name: The name of the dependency.
            value: The provided value for the dependency.

        Returns:
            The resolved dependency value, either from the provided value or the service object.
        """
        if value is not None:
            return value
        if svc is None:
            return None
        return getattr(svc, name, None)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        """Render the home page.

        Args:
            request: The incoming HTTP request.

        Returns:
            The HTML response for the home page.
        """
        resolved_templates = _dep("templates", templates)
        resolved_config = _dep("config", config)
        resolved_is_authorised_request = _dep("_is_authorised_request", is_authorised_request)
        resolved_unauthorised_message = _dep("_unauthorised_message", unauthorised_message)
        resolved_branding_ctx = _dep("_branding_ctx", branding_ctx)
        resolved_resolve_query_model_capabilities = _dep(
            "_resolve_query_model_capabilities",
            resolve_query_model_capabilities,
        )
        auth_token = _extract_auth_token(request)

        if resolved_is_authorised_request is None or not resolved_is_authorised_request(
            auth_token, request
        ):
            if callable(resolved_unauthorised_message):
                message = resolved_unauthorised_message(request)
            else:
                message = "Unauthorised."
            return HTMLResponse(content=message, status_code=401)

        if (
            resolved_templates is None
            or resolved_config is None
            or not callable(resolved_branding_ctx)
        ):
            return HTMLResponse(content="Home endpoint misconfigured.", status_code=500)

        branding_context = resolved_branding_ctx()
        if not isinstance(branding_context, dict):
            return HTMLResponse(content="Home endpoint misconfigured.", status_code=500)

        resolved_capabilities: dict[str, Any] | None = None
        if callable(resolved_resolve_query_model_capabilities):
            try:
                candidate = resolved_resolve_query_model_capabilities()
                if isinstance(candidate, dict):
                    resolved_capabilities = candidate
            except Exception:
                resolved_capabilities = None

        (
            ui_query_cap,
            ui_evaluator_cap,
            runtime_hints_ui,
        ) = _effective_completion_limits(resolved_config, resolved_capabilities)

        return resolved_templates.TemplateResponse(
            request,
            "index.html",
            {
                **branding_context,
                "question": "",
                "answer": "",
                "results": [],
                "controls_results": [],
                "controls_debug": None,
                "error": "",
                "evaluation": None,
                "metrics": None,
                "iterations": None,
                "retrieve_k": resolved_config.search_top_k,
                "controls_context_cap": 4,
                "temperature": resolved_config.default_temperature,
                "top_p": getattr(resolved_config, "top_p", 1.0),
                "max_completion_tokens": ui_query_cap,
                "evaluator_max_completion_tokens": ui_evaluator_cap,
                "controls_semantic": resolved_config.controls_semantic_default,
                "controls_framework": "",
                "controls_comparison_mode": "auto-detect",
                "include_graph_expansion": False,
                "graph_expansion_depth": None,
                "graph_expansion_max_edges": None,
                "evidence_corpora_include": [],
                "advanced_mode": False,
                "thinking_mode": "balanced",
                "thinking_mode_presets": _thinking_mode_presets_for_ui(
                    default_max_completion_tokens=ui_query_cap,
                    default_evaluator_max_completion_tokens=ui_evaluator_cap,
                ),
                "runtime_hints_ui": runtime_hints_ui,
                "auth_token": auth_token,
                "index_name": resolved_config.search_index_name,
                "embedding_deployment": resolved_config.embedding_deployment,
                "query_deployment": resolved_config.query_deployment,
                "query_model_display": _query_model_display(resolved_config),
                "evaluation_threshold": resolved_config.evaluation_threshold,
                "auth_enabled": bool(resolved_config.auth_token),
                "user_id": "",
                "session_id": "",
                "conversation_id": "",
            },
        )
