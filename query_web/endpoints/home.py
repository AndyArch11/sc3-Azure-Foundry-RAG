"""Home page endpoint registration."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse


def register_home_endpoints(app: Any, svc: Any) -> None:
    """Register home page endpoints."""

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        if not svc._is_authorised_request("", request):
            return HTMLResponse(content=svc._unauthorised_message(request), status_code=401)

        return svc.templates.TemplateResponse(
            request,
            "index.html",
            {
                **svc._branding_ctx(),
                "question": "",
                "answer": "",
                "results": [],
                "controls_results": [],
                "controls_debug": None,
                "error": "",
                "evaluation": None,
                "metrics": None,
                "iterations": None,
                "retrieve_k": svc.config.search_top_k,
                "temperature": svc.config.default_temperature,
                "controls_semantic": svc.config.controls_semantic_default,
                "controls_framework": "",
                "controls_comparison_mode": "auto-detect",
                "evidence_corpora_include": [],
                "advanced_mode": False,
                "auth_token": "",
                "index_name": svc.config.search_index_name,
                "embedding_deployment": svc.config.embedding_deployment,
                "query_deployment": svc.config.query_deployment,
                "evaluation_threshold": svc.config.evaluation_threshold,
                "auth_enabled": bool(svc.config.auth_token),
                "user_id": "",
                "session_id": "",
                "conversation_id": "",
            },
        )
