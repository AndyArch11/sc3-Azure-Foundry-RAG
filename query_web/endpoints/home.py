"""Home page endpoint registration."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse


def _extract_auth_token(request: Request) -> str:
    """Best-effort auth token extraction for initial page loads."""
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
) -> None:
    """Register home page endpoints."""

    def _query_model_display(resolved_config: Any) -> str:
        provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower()
        if provider in {"local", "dev"}:
            return os.getenv("OLLAMA_MODEL", "llama3").strip() or "llama3"
        return str(getattr(resolved_config, "query_deployment", "")).strip()

    def _dep(name: str, value: Any) -> Any:
        if value is not None:
            return value
        if svc is None:
            return None
        return getattr(svc, name, None)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        resolved_templates = _dep("templates", templates)
        resolved_config = _dep("config", config)
        resolved_is_authorised_request = _dep("_is_authorised_request", is_authorised_request)
        resolved_unauthorised_message = _dep("_unauthorised_message", unauthorised_message)
        resolved_branding_ctx = _dep("_branding_ctx", branding_ctx)
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
                "temperature": resolved_config.default_temperature,
                "max_completion_tokens": getattr(resolved_config, "max_completion_tokens", 1400),
                "evaluator_max_completion_tokens": getattr(
                    resolved_config,
                    "evaluator_max_completion_tokens",
                    800,
                ),
                "controls_semantic": resolved_config.controls_semantic_default,
                "controls_framework": "",
                "controls_comparison_mode": "auto-detect",
                "evidence_corpora_include": [],
                "advanced_mode": False,
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
