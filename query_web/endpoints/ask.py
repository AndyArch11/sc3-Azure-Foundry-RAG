"""Ask endpoint registration."""

# The endpoint layer intentionally delegates into `svc` helper methods that are
# named with leading underscores in `query_web.app` for backward compatibility.
# pylint: disable=protected-access,broad-exception-caught,too-many-positional-arguments

from __future__ import annotations

import logging
from typing import Any

from fastapi import Form, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)


def register_ask_endpoints(
    app: Any,
    svc: Any,
    *,
    ask_request_model: Any,
    ask_response_model: Any,
) -> None:
    """Register ask form and API endpoints."""

    @app.post("/ask", response_class=HTMLResponse)
    def ask(
        request: Request,
        question: str = Form(...),
        retrieve_k: int = Form(...),
        temperature: float = Form(...),
        controls_semantic: str = Form(""),
        controls_framework: str = Form(""),
        controls_comparison_mode: str = Form("auto-detect"),
        evidence_corpora_include: list[str] = Form(default=[]),
        advanced_mode: str = Form(""),
        auth_token: str = Form(""),
        session_id: str = Form(default=""),
        conversation_id: str = Form(default=""),
    ) -> HTMLResponse:
        user_id = svc._get_user_id(auth_token, session_id)
        session = None
        advanced_mode_enabled = svc._form_bool(advanced_mode, default=False)

        if not svc._is_authorised_request(auth_token, request):
            return svc.templates.TemplateResponse(
                request,
                "index.html",
                {
                    **svc._branding_ctx(),
                    "question": question,
                    "answer": "",
                    "results": [],
                    "controls_results": [],
                    "controls_debug": None,
                    "error": svc._unauthorised_message(request),
                    "evaluation": None,
                    "metrics": None,
                    "iterations": None,
                    "retrieve_k": retrieve_k,
                    "temperature": temperature,
                    "controls_semantic": svc._form_bool(
                        controls_semantic, default=svc.config.controls_semantic_default
                    ),
                    "controls_framework": (controls_framework or "").strip().lower(),
                    "controls_comparison_mode": svc._normalise_controls_comparison_mode(
                        controls_comparison_mode
                    ),
                    "evidence_corpora_include": evidence_corpora_include,
                    "advanced_mode": advanced_mode_enabled,
                    "auth_token": "",
                    "index_name": svc.config.search_index_name,
                    "embedding_deployment": svc.config.embedding_deployment,
                    "query_deployment": svc.config.query_deployment,
                    "evaluation_threshold": svc.config.evaluation_threshold,
                    "auth_enabled": bool(svc.config.auth_token),
                    "user_id": user_id,
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                },
                status_code=401,
            )

        if session_id and conversation_id:
            session = svc._load_conversation(user_id, conversation_id)

        retrieve_k = max(1, min(20, retrieve_k))
        temperature = max(0, min(1.0, temperature))
        controls_semantic_enabled = svc._form_bool(
            controls_semantic, default=svc.config.controls_semantic_default
        )
        controls_framework_value = (controls_framework or "").strip().lower()
        controls_framework_filter = svc._normalise_framework_filter(controls_framework_value)
        controls_comparison_mode_value = svc._normalise_controls_comparison_mode(
            controls_comparison_mode
        )
        evidence_corpora_include_filter = (
            svc._normalise_evidence_corpora(evidence_corpora_include)
            if evidence_corpora_include
            else None
        )
        evidence_corpora_exclude_filter: list[str] | None = None

        try:
            conversation_history = session.messages if session else []
            feedback_context = svc._build_feedback_context(session) if session else ""

            result = svc._run_rag(
                question=question,
                retrieve_k=retrieve_k,
                temperature=temperature,
                controls_semantic=controls_semantic_enabled,
                controls_framework=controls_framework_filter,
                controls_comparison_mode=controls_comparison_mode_value,
                evidence_corpora_include=evidence_corpora_include_filter,
                evidence_corpora_exclude=evidence_corpora_exclude_filter,
                conversation_history=conversation_history,
                feedback_context=feedback_context,
            )

            if session:
                session.messages.append(svc.ConversationMessage(role="user", content=question))
                session.messages.append(
                    svc.ConversationMessage(role="assistant", content=result["answer"])
                )
                session.updated_at = svc._utc_now_iso()
                svc._save_conversation(session)

            error = ""
        except Exception as exc:
            logger.exception("Failed to process /ask request: %s", exc)
            result = {
                "answer": "",
                "results": [],
                "controls_results": [],
                "controls_debug": None,
                "evaluation": None,
                "metrics": None,
                "iterations": None,
            }
            error = svc._INTERNAL_ERROR_MESSAGE

        return svc.templates.TemplateResponse(
            request,
            "index.html",
            {
                **svc._branding_ctx(),
                "question": question,
                "answer": result["answer"],
                "results": result["results"],
                "controls_results": result.get("controls_results", []),
                "controls_debug": result.get("controls_debug"),
                "error": error,
                "evaluation": result["evaluation"],
                "metrics": result["metrics"],
                "iterations": result["iterations"],
                "retrieve_k": retrieve_k,
                "temperature": temperature,
                "controls_semantic": controls_semantic_enabled,
                "controls_framework": controls_framework_value,
                "controls_comparison_mode": controls_comparison_mode_value,
                "evidence_corpora_include": evidence_corpora_include,
                "advanced_mode": advanced_mode_enabled,
                "auth_token": auth_token,
                "index_name": svc.config.search_index_name,
                "embedding_deployment": svc.config.embedding_deployment,
                "query_deployment": svc.config.query_deployment,
                "evaluation_threshold": svc.config.evaluation_threshold,
                "auth_enabled": bool(svc.config.auth_token),
                "user_id": user_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
            },
        )

    @app.post("/api/ask", response_model=ask_response_model)
    def ask_api(request: Request, payload: dict[str, Any]) -> Any:
        parsed_payload = ask_request_model.model_validate(payload)
        question = parsed_payload.question.strip()
        if not question:
            return ask_response_model(
                answer="",
                results=[],
                controls_results=[],
                controls_debug=None,
                evaluation=None,
                iterations=None,
                metrics=None,
                audit=None,
                error="Question must not be empty.",
            )

        if not svc._is_authorised_request(parsed_payload.auth_token, request):
            return ask_response_model(
                answer="",
                results=[],
                controls_results=[],
                controls_debug=None,
                evaluation=None,
                iterations=None,
                metrics=None,
                audit=None,
                error=svc._unauthorised_message(request),
            )

        try:
            result = svc._run_rag(
                question=question,
                retrieve_k=parsed_payload.retrieve_k,
                temperature=parsed_payload.temperature,
                controls_semantic=(
                    parsed_payload.controls_semantic
                    if parsed_payload.controls_semantic is not None
                    else svc.config.controls_semantic_default
                ),
                controls_framework=svc._normalise_framework_filter(
                    parsed_payload.controls_framework
                ),
                controls_comparison_mode=svc._normalise_controls_comparison_mode(
                    parsed_payload.controls_comparison_mode
                ),
                evidence_corpora_include=svc._normalise_evidence_corpora(
                    parsed_payload.evidence_corpora_include
                ),
                evidence_corpora_exclude=svc._normalise_evidence_corpora(
                    parsed_payload.evidence_corpora_exclude
                ),
            )
            return ask_response_model(
                answer=result["answer"],
                results=result["results"],
                controls_results=result.get("controls_results", []),
                controls_debug=result.get("controls_debug"),
                evaluation=result["evaluation"],
                iterations=result["iterations"],
                metrics=result["metrics"],
                audit=result.get("audit"),
                error="",
            )
        except Exception as exc:
            logger.exception("Failed to process /api/ask request: %s", exc)
            return ask_response_model(
                answer="",
                results=[],
                controls_results=[],
                controls_debug=None,
                evaluation=None,
                iterations=None,
                metrics=None,
                audit=None,
                error=svc._INTERNAL_ERROR_MESSAGE,
            )
