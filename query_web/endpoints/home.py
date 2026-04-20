"""Home page endpoint registration."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse


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

        if resolved_is_authorised_request is None or not resolved_is_authorised_request("", request):
            if callable(resolved_unauthorised_message):
                message = resolved_unauthorised_message(request)
            else:
                message = "Unauthorised."
            return HTMLResponse(content=message, status_code=401)

        if resolved_templates is None or resolved_config is None or not callable(resolved_branding_ctx):
            return HTMLResponse(content="Home endpoint misconfigured.", status_code=500)

        return resolved_templates.TemplateResponse(
            request,
            "index.html",
            {
                **resolved_branding_ctx(),
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
                "auth_token": "",
                "index_name": resolved_config.search_index_name,
                "embedding_deployment": resolved_config.embedding_deployment,
                "query_deployment": resolved_config.query_deployment,
                "evaluation_threshold": resolved_config.evaluation_threshold,
                "auth_enabled": bool(resolved_config.auth_token),
                "user_id": "",
                "session_id": "",
                "conversation_id": "",
            },
        )
