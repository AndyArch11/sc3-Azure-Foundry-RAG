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
    svc: Any | None = None,
    *,
    ask_request_model: Any,
    ask_response_model: Any,
    templates: Any | None = None,
    config: Any | None = None,
    conversation_message_cls: Any | None = None,
    get_user_id: Any | None = None,
    form_bool: Any | None = None,
    is_authorised_request: Any | None = None,
    unauthorised_message: Any | None = None,
    normalise_controls_comparison_mode: Any | None = None,
    normalise_framework_filter: Any | None = None,
    normalise_evidence_corpora: Any | None = None,
    load_conversation: Any | None = None,
    build_feedback_context: Any | None = None,
    run_rag: Any | None = None,
    save_conversation: Any | None = None,
    utc_now_iso: Any | None = None,
    branding_ctx: Any | None = None,
    internal_error_message: str | None = None,
) -> None:
    """Register ask form and API endpoints."""

    def _dep(name: str, value: Any) -> Any:
        if value is not None:
            return value
        if svc is None:
            return None
        return getattr(svc, name, None)

    @app.post("/ask", response_class=HTMLResponse)
    def ask(
        request: Request,
        question: str = Form(...),
        retrieve_k: int = Form(...),
        temperature: float = Form(...),
        max_completion_tokens: str = Form(""),
        evaluator_max_completion_tokens: str = Form(""),
        controls_semantic: str = Form(""),
        controls_framework: str = Form(""),
        controls_comparison_mode: str = Form("auto-detect"),
        evidence_corpora_include: list[str] = Form(default=[]),
        advanced_mode: str = Form(""),
        auth_token: str = Form(""),
        session_id: str = Form(default=""),
        conversation_id: str = Form(default=""),
    ) -> HTMLResponse:
        resolved_templates = _dep("templates", templates)
        resolved_config = _dep("config", config)
        resolved_conversation_message_cls = _dep("ConversationMessage", conversation_message_cls)
        resolved_get_user_id = _dep("_get_user_id", get_user_id)
        resolved_form_bool = _dep("_form_bool", form_bool)
        resolved_is_authorised_request = _dep("_is_authorised_request", is_authorised_request)
        resolved_unauthorised_message = _dep("_unauthorised_message", unauthorised_message)
        resolved_normalise_controls_comparison_mode = _dep(
            "_normalise_controls_comparison_mode", normalise_controls_comparison_mode
        )
        resolved_normalise_framework_filter = _dep(
            "_normalise_framework_filter", normalise_framework_filter
        )
        resolved_normalise_evidence_corpora = _dep(
            "_normalise_evidence_corpora", normalise_evidence_corpora
        )
        resolved_load_conversation = _dep("_load_conversation", load_conversation)
        resolved_build_feedback_context = _dep("_build_feedback_context", build_feedback_context)
        resolved_run_rag = _dep("_run_rag", run_rag)
        resolved_save_conversation = _dep("_save_conversation", save_conversation)
        resolved_utc_now_iso = _dep("_utc_now_iso", utc_now_iso)
        resolved_branding_ctx = _dep("_branding_ctx", branding_ctx)
        resolved_internal_error_message = (
            internal_error_message
            if internal_error_message is not None
            else _dep("_INTERNAL_ERROR_MESSAGE", None)
        )

        if (
            resolved_get_user_id is None
            or resolved_form_bool is None
            or resolved_is_authorised_request is None
            or resolved_unauthorised_message is None
            or resolved_normalise_controls_comparison_mode is None
            or resolved_normalise_framework_filter is None
            or resolved_normalise_evidence_corpora is None
            or resolved_run_rag is None
            or resolved_branding_ctx is None
            or resolved_templates is None
            or resolved_config is None
            or resolved_internal_error_message is None
        ):
            return HTMLResponse(content="Ask endpoint misconfigured.", status_code=500)

        user_id = resolved_get_user_id(auth_token, session_id)
        session = None
        advanced_mode_enabled = resolved_form_bool(advanced_mode, default=False)
        max_tokens_value = (max_completion_tokens or "").strip()
        evaluator_tokens_value = (evaluator_max_completion_tokens or "").strip()
        try:
            max_completion_tokens_int = (
                max(256, min(8192, int(max_tokens_value))) if max_tokens_value else None
            )
        except ValueError:
            max_completion_tokens_int = None
        try:
            evaluator_max_completion_tokens_int = (
                max(128, min(4096, int(evaluator_tokens_value))) if evaluator_tokens_value else None
            )
        except ValueError:
            evaluator_max_completion_tokens_int = None

        if not resolved_is_authorised_request(auth_token, request):
            return resolved_templates.TemplateResponse(
                request,
                "index.html",
                {
                    **resolved_branding_ctx(),
                    "question": question,
                    "answer": "",
                    "results": [],
                    "controls_results": [],
                    "controls_debug": None,
                    "error": resolved_unauthorised_message(request),
                    "evaluation": None,
                    "metrics": None,
                    "iterations": None,
                    "retrieve_k": retrieve_k,
                    "temperature": temperature,
                    "max_completion_tokens": (
                        max_completion_tokens_int
                        if max_completion_tokens_int is not None
                        else getattr(resolved_config, "max_completion_tokens", 1400)
                    ),
                    "evaluator_max_completion_tokens": (
                        evaluator_max_completion_tokens_int
                        if evaluator_max_completion_tokens_int is not None
                        else getattr(resolved_config, "evaluator_max_completion_tokens", 800)
                    ),
                    "controls_semantic": resolved_form_bool(
                        controls_semantic, default=resolved_config.controls_semantic_default
                    ),
                    "controls_framework": (controls_framework or "").strip().lower(),
                    "controls_comparison_mode": resolved_normalise_controls_comparison_mode(
                        controls_comparison_mode
                    ),
                    "evidence_corpora_include": evidence_corpora_include,
                    "advanced_mode": advanced_mode_enabled,
                    "auth_token": "",
                    "index_name": resolved_config.search_index_name,
                    "embedding_deployment": resolved_config.embedding_deployment,
                    "query_deployment": resolved_config.query_deployment,
                    "evaluation_threshold": resolved_config.evaluation_threshold,
                    "auth_enabled": bool(resolved_config.auth_token),
                    "user_id": user_id,
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                },
                status_code=401,
            )

        if session_id and conversation_id and resolved_load_conversation is not None:
            session = resolved_load_conversation(user_id, conversation_id)

        retrieve_k = max(1, min(20, retrieve_k))
        temperature = max(0, min(1.0, temperature))
        controls_semantic_enabled = resolved_form_bool(
            controls_semantic, default=resolved_config.controls_semantic_default
        )
        controls_framework_value = (controls_framework or "").strip().lower()
        controls_framework_filter = resolved_normalise_framework_filter(controls_framework_value)
        controls_comparison_mode_value = resolved_normalise_controls_comparison_mode(
            controls_comparison_mode
        )
        evidence_corpora_include_filter = (
            resolved_normalise_evidence_corpora(evidence_corpora_include)
            if evidence_corpora_include
            else None
        )
        evidence_corpora_exclude_filter: list[str] | None = None

        try:
            conversation_history = session.messages if session else []
            feedback_context = (
                resolved_build_feedback_context(session)
                if session and resolved_build_feedback_context is not None
                else ""
            )

            result = resolved_run_rag(
                question=question,
                retrieve_k=retrieve_k,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens_int,
                evaluator_max_completion_tokens=evaluator_max_completion_tokens_int,
                controls_semantic=controls_semantic_enabled,
                controls_framework=controls_framework_filter,
                controls_comparison_mode=controls_comparison_mode_value,
                evidence_corpora_include=evidence_corpora_include_filter,
                evidence_corpora_exclude=evidence_corpora_exclude_filter,
                conversation_history=conversation_history,
                feedback_context=feedback_context,
            )

            if session:
                if resolved_conversation_message_cls is None:
                    raise RuntimeError("ConversationMessage dependency is missing.")
                session.messages.append(resolved_conversation_message_cls(role="user", content=question))
                session.messages.append(
                    resolved_conversation_message_cls(role="assistant", content=result["answer"])
                )
                if resolved_utc_now_iso is not None:
                    session.updated_at = resolved_utc_now_iso()
                if resolved_save_conversation is not None:
                    resolved_save_conversation(session)

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
            error = resolved_internal_error_message

        return resolved_templates.TemplateResponse(
            request,
            "index.html",
            {
                **resolved_branding_ctx(),
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
                "max_completion_tokens": (
                    max_completion_tokens_int
                    if max_completion_tokens_int is not None
                    else getattr(resolved_config, "max_completion_tokens", 1400)
                ),
                "evaluator_max_completion_tokens": (
                    evaluator_max_completion_tokens_int
                    if evaluator_max_completion_tokens_int is not None
                    else getattr(resolved_config, "evaluator_max_completion_tokens", 800)
                ),
                "controls_semantic": controls_semantic_enabled,
                "controls_framework": controls_framework_value,
                "controls_comparison_mode": controls_comparison_mode_value,
                "evidence_corpora_include": evidence_corpora_include,
                "advanced_mode": advanced_mode_enabled,
                "auth_token": auth_token,
                "index_name": resolved_config.search_index_name,
                "embedding_deployment": resolved_config.embedding_deployment,
                "query_deployment": resolved_config.query_deployment,
                "evaluation_threshold": resolved_config.evaluation_threshold,
                "auth_enabled": bool(resolved_config.auth_token),
                "user_id": user_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
            },
        )

    @app.post("/api/ask", response_model=ask_response_model)
    def ask_api(request: Request, payload: dict[str, Any]) -> Any:
        resolved_config = _dep("config", config)
        resolved_is_authorised_request = _dep("_is_authorised_request", is_authorised_request)
        resolved_unauthorised_message = _dep("_unauthorised_message", unauthorised_message)
        resolved_normalise_controls_comparison_mode = _dep(
            "_normalise_controls_comparison_mode", normalise_controls_comparison_mode
        )
        resolved_normalise_framework_filter = _dep(
            "_normalise_framework_filter", normalise_framework_filter
        )
        resolved_normalise_evidence_corpora = _dep(
            "_normalise_evidence_corpora", normalise_evidence_corpora
        )
        resolved_run_rag = _dep("_run_rag", run_rag)
        resolved_internal_error_message = (
            internal_error_message
            if internal_error_message is not None
            else _dep("_INTERNAL_ERROR_MESSAGE", None)
        )

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

        if (
            resolved_is_authorised_request is None
            or resolved_unauthorised_message is None
            or resolved_run_rag is None
            or resolved_config is None
            or resolved_normalise_framework_filter is None
            or resolved_normalise_controls_comparison_mode is None
            or resolved_normalise_evidence_corpora is None
            or resolved_internal_error_message is None
        ):
            return ask_response_model(
                answer="",
                results=[],
                controls_results=[],
                controls_debug=None,
                evaluation=None,
                iterations=None,
                metrics=None,
                audit=None,
                error="Ask API endpoint misconfigured.",
            )

        if not resolved_is_authorised_request(parsed_payload.auth_token, request):
            return ask_response_model(
                answer="",
                results=[],
                controls_results=[],
                controls_debug=None,
                evaluation=None,
                iterations=None,
                metrics=None,
                audit=None,
                error=resolved_unauthorised_message(request),
            )

        try:
            result = resolved_run_rag(
                question=question,
                retrieve_k=parsed_payload.retrieve_k,
                temperature=parsed_payload.temperature,
                max_completion_tokens=getattr(parsed_payload, "max_completion_tokens", None),
                evaluator_max_completion_tokens=getattr(
                    parsed_payload, "evaluator_max_completion_tokens", None
                ),
                controls_semantic=(
                    parsed_payload.controls_semantic
                    if parsed_payload.controls_semantic is not None
                    else resolved_config.controls_semantic_default
                ),
                controls_framework=resolved_normalise_framework_filter(
                    parsed_payload.controls_framework
                ),
                controls_comparison_mode=resolved_normalise_controls_comparison_mode(
                    parsed_payload.controls_comparison_mode
                ),
                evidence_corpora_include=resolved_normalise_evidence_corpora(
                    parsed_payload.evidence_corpora_include
                ),
                evidence_corpora_exclude=resolved_normalise_evidence_corpora(
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
                error=resolved_internal_error_message,
            )
