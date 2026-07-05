"""Ask endpoint registration."""

# The endpoint layer intentionally delegates into `svc` helper methods that are
# named with leading underscores in `query_web.app` for backward compatibility.
# pylint: disable=protected-access,broad-exception-caught,too-many-positional-arguments

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from query_web.config import (
    _normalise_thinking_mode,
    _thinking_defaults,
    _thinking_mode_presets_for_ui,
)
from query_web.request_context import get_correlation_id
from runtime.provider_core import normalise_cloud_provider

logger = logging.getLogger(__name__)


def _has_missing_dependencies(values: list[Any]) -> bool:
    """Return True when at least one required dependency is missing.

    Args:
        values: A list of dependency values to check.

    Returns:
        True if at least one required dependency is missing, False otherwise.
    """
    return any(value is None for value in values)


def _user_visible_ask_error(default_message: str, exc: Exception) -> str:
    """Map internal failures to safe, actionable user-facing ask errors.

    Args:
        default_message: The default error message to return.
        exc: The exception that was raised.

    Returns:
        A user-friendly error message.
    """
    message = str(exc).lower()
    if "ollama" in message and ("timed out" in message or "readtimeout" in message):
        return (
            "The local Ollama model timed out while generating the answer. "
            "Try again, shorten the question, or reduce completion tokens."
        )
    return default_message


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
    """Register ask form and API endpoints.

    Args:
        app: The FastAPI application instance.
        svc: Optional service object containing helper methods.
        ask_request_model: Pydantic model for ask API request validation.
        ask_response_model: Pydantic model for ask API response validation.
        templates: Optional Jinja2 templates object for rendering HTML responses.
        config: Optional configuration object.
        conversation_message_cls: Optional class for conversation messages.
        get_user_id: Optional function to retrieve user ID from auth token.
        form_bool: Optional function to parse boolean form values.
        is_authorised_request: Optional function to check request authorisation.
        unauthorised_message: Optional function to generate unauthorised message.
        normalise_controls_comparison_mode: Optional function to normalise controls comparison mode.
        normalise_framework_filter: Optional function to normalise framework filter.
        normalise_evidence_corpora: Optional function to normalise evidence corpora.
        load_conversation: Optional function to load conversation from storage.
        build_feedback_context: Optional function to build feedback context.
        run_rag: Optional function to run the RAG process.
        save_conversation: Optional function to save conversation to storage.
        utc_now_iso: Optional function to get current UTC time in ISO format.
        branding_ctx: Optional function to get branding context for templates.
        internal_error_message: Optional default message for internal errors.

    Rearranges the ask endpoints to use the provided dependencies, allowing for flexible configuration and testing.
    """
    # pylint: disable=too-many-statements

    def _query_model_display(resolved_config: Any) -> str:
        """Determine the display name for the query model based on the cloud provider.

        Args:
            resolved_config: The resolved configuration object.

        Returns:
            A string representing the display name of the query model.
        """
        try:
            provider = normalise_cloud_provider(os.getenv("CLOUD_PROVIDER"))
        except ValueError:
            provider = "azure"
        if provider == "local":
            return os.getenv("OLLAMA_MODEL", "llama3").strip() or "llama3"
        return str(getattr(resolved_config, "query_deployment", "")).strip()

    def _dep(name: str, value: Any) -> Any:
        """Retrieve a dependency value, either from the provided value or from the service object.

        Args:
            name: The name of the dependency.
            value: The provided value for the dependency.

        Returns:
            The resolved dependency value.
        """
        if value is not None:
            return value
        if svc is None:
            return None
        return getattr(svc, name, None)

    @app.get("/ask", response_class=HTMLResponse)
    def ask_get(request: Request) -> RedirectResponse:
        """Redirect GET requests to the ask endpoint to the root with optional auth token.

        Args:
            request: The incoming HTTP request.

        Returns:
            A RedirectResponse to the root URL with the optional auth token.
        """
        auth_token = str(request.query_params.get("auth_token", "")).strip()
        if auth_token:
            return RedirectResponse(url=f"/?auth_token={auth_token}", status_code=307)
        return RedirectResponse(url="/", status_code=307)

    @app.post("/ask", response_class=HTMLResponse)
    def ask(
        request: Request,
        question: str = Form(...),
        retrieve_k: int = Form(...),
        controls_context_cap: int = Form(0),
        temperature: float = Form(...),
        top_p: float = Form(1.0),
        max_completion_tokens: str = Form(""),
        evaluator_max_completion_tokens: str = Form(""),
        controls_semantic: str = Form(""),
        controls_framework: str = Form(""),
        controls_comparison_mode: str = Form("auto-detect"),
        evidence_corpora_include: list[str] = Form(default=[]),
        advanced_mode: str = Form(""),
        thinking_mode: str = Form(default=""),
        auth_token: str = Form(""),
        session_id: str = Form(default=""),
        conversation_id: str = Form(default=""),
    ) -> HTMLResponse:
        """Handle POST requests to the ask endpoint, processing the question and returning an HTML response.

        Args:
            request: The incoming HTTP request.
            question: The user's question from the form.
            retrieve_k: The number of top results to retrieve.
            controls_context_cap: The maximum context capacity for controls.
            temperature: The temperature setting for the model.
            top_p: The top-p setting for the model.
            max_completion_tokens: The maximum number of tokens for completion.
            evaluator_max_completion_tokens: The maximum number of tokens for the evaluator.
            controls_semantic: The semantic controls setting.
            controls_framework: The framework controls setting.
            controls_comparison_mode: The comparison mode for controls.
            evidence_corpora_include: The list of evidence corpora to include.
            advanced_mode: The advanced mode setting.
            thinking_mode: The thinking mode setting.
            auth_token: The authentication token.
            session_id: The session ID.
            conversation_id: The conversation ID.

        Returns:
            An HTMLResponse containing the rendered template with the results of the ask operation.
        """
        # pylint: disable=too-many-statements
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

        required_dependencies = [
            resolved_get_user_id,
            resolved_form_bool,
            resolved_is_authorised_request,
            resolved_unauthorised_message,
            resolved_normalise_controls_comparison_mode,
            resolved_normalise_framework_filter,
            resolved_normalise_evidence_corpora,
            resolved_run_rag,
            resolved_branding_ctx,
            resolved_templates,
            resolved_config,
            resolved_internal_error_message,
        ]
        if _has_missing_dependencies(required_dependencies):
            return HTMLResponse(content="Ask endpoint misconfigured.", status_code=500)

        user_id = resolved_get_user_id(auth_token, session_id)
        session = None
        advanced_mode_enabled = resolved_form_bool(advanced_mode, default=False)

        # Apply thinking mode presets if provided, allowing explicit form values to override
        normalised_thinking_mode = _normalise_thinking_mode(
            thinking_mode or os.getenv("THINKING_MODE")
        )
        mode_defaults = _thinking_defaults(
            mode=normalised_thinking_mode,
            default_max_completion_tokens=getattr(resolved_config, "max_completion_tokens", 1400),
            default_evaluator_max_completion_tokens=getattr(
                resolved_config, "evaluator_max_completion_tokens", 800
            ),
        )

        # Use mode presets if form values are empty, otherwise use explicit form values
        if not (retrieve_k and retrieve_k != 0):
            retrieve_k = int(mode_defaults.get("search_top_k", 5))
        if controls_context_cap <= 0:
            controls_context_cap = int(
                mode_defaults.get("controls_top_k", getattr(resolved_config, "controls_top_k", 4))
            )
        if temperature is None or temperature == 0.0:
            temperature = float(mode_defaults.get("default_temperature", 1.0))
        if top_p is None or top_p == 1.0:
            top_p = float(mode_defaults.get("top_p", getattr(resolved_config, "top_p", 1.0)))

        max_tokens_value = (max_completion_tokens or "").strip()
        evaluator_tokens_value = (evaluator_max_completion_tokens or "").strip()
        try:
            max_completion_tokens_int = (
                max(256, min(8192, int(max_tokens_value))) if max_tokens_value else None
            )
            if max_completion_tokens_int is None:
                max_completion_tokens_int = int(
                    mode_defaults.get(
                        "max_completion_tokens",
                        getattr(resolved_config, "max_completion_tokens", 1400),
                    )
                )
        except ValueError:
            max_completion_tokens_int = int(
                mode_defaults.get(
                    "max_completion_tokens",
                    getattr(resolved_config, "max_completion_tokens", 1400),
                )
            )
        try:
            evaluator_max_completion_tokens_int = (
                max(128, min(4096, int(evaluator_tokens_value))) if evaluator_tokens_value else None
            )
            if evaluator_max_completion_tokens_int is None:
                evaluator_max_completion_tokens_int = int(
                    mode_defaults.get(
                        "evaluator_max_completion_tokens",
                        getattr(resolved_config, "evaluator_max_completion_tokens", 800),
                    )
                )
        except ValueError:
            evaluator_max_completion_tokens_int = int(
                mode_defaults.get(
                    "evaluator_max_completion_tokens",
                    getattr(resolved_config, "evaluator_max_completion_tokens", 800),
                )
            )
        thinking_mode_presets = _thinking_mode_presets_for_ui(
            default_max_completion_tokens=getattr(resolved_config, "max_completion_tokens", 1400),
            default_evaluator_max_completion_tokens=getattr(
                resolved_config,
                "evaluator_max_completion_tokens",
                800,
            ),
        )

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
                    "controls_context_cap": controls_context_cap,
                    "temperature": temperature,
                    "top_p": top_p,
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
                    "thinking_mode": normalised_thinking_mode,
                    "thinking_mode_presets": thinking_mode_presets,
                    "auth_token": "",
                    "index_name": resolved_config.search_index_name,
                    "embedding_deployment": resolved_config.embedding_deployment,
                    "query_deployment": resolved_config.query_deployment,
                    "query_model_display": _query_model_display(resolved_config),
                    "evaluation_threshold": resolved_config.evaluation_threshold,
                    "auth_enabled": bool(resolved_config.auth_token),
                    "user_id": user_id,
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                },
                status_code=401,
            )

        if session_id and conversation_id and resolved_load_conversation is not None:
            session = resolved_load_conversation(
                user_id,
                conversation_id,
                correlation_id=get_correlation_id(request),
            )

        retrieve_k = max(1, min(20, retrieve_k))
        controls_context_cap = max(1, min(2000, controls_context_cap))
        temperature = max(0, min(1.0, temperature))
        top_p = max(0.0, min(1.0, top_p))
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
                controls_context_cap=controls_context_cap,
                temperature=temperature,
                top_p=top_p,
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
                session.messages.append(
                    resolved_conversation_message_cls(role="user", content=question)
                )
                session.messages.append(
                    resolved_conversation_message_cls(role="assistant", content=result["answer"])
                )
                if resolved_utc_now_iso is not None:
                    session.updated_at = resolved_utc_now_iso()
                if resolved_save_conversation is not None:
                    resolved_save_conversation(
                        session,
                        correlation_id=get_correlation_id(request),
                    )

            error = ""
        except Exception as exc:
            logger.exception(
                "Failed to process ask request",
                extra={
                    "event": "ask_failed",
                    "endpoint": "/ask",
                    "exc_type": type(exc).__name__,
                },
            )
            result = {
                "answer": "",
                "results": [],
                "controls_results": [],
                "controls_debug": None,
                "evaluation": None,
                "metrics": None,
                "iterations": None,
            }
            error = _user_visible_ask_error(resolved_internal_error_message, exc)

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
                "controls_context_cap": controls_context_cap,
                "temperature": temperature,
                "top_p": top_p,
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
                "thinking_mode": normalised_thinking_mode,
                "thinking_mode_presets": thinking_mode_presets,
                "auth_token": auth_token,
                "index_name": resolved_config.search_index_name,
                "embedding_deployment": resolved_config.embedding_deployment,
                "query_deployment": resolved_config.query_deployment,
                "query_model_display": _query_model_display(resolved_config),
                "evaluation_threshold": resolved_config.evaluation_threshold,
                "auth_enabled": bool(resolved_config.auth_token),
                "user_id": user_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
            },
        )

    @app.post("/api/ask", response_model=ask_response_model)
    def ask_api(request: Request, payload: dict[str, Any]) -> Any:
        """Handle POST requests to the ask API endpoint, processing the question and returning a structured response.

        Args:
            request: The incoming HTTP request.
            payload: The JSON payload containing the ask request parameters.


        Returns:
            A structured response containing the answer, results, and any relevant metadata or error messages.
        """
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

        # Apply thinking mode presets if provided, allowing explicit values to override
        api_thinking_mode = getattr(parsed_payload, "thinking_mode", "balanced") or "balanced"
        normalised_api_thinking_mode = _normalise_thinking_mode(api_thinking_mode)
        api_mode_defaults = _thinking_defaults(
            mode=normalised_api_thinking_mode,
            default_max_completion_tokens=getattr(resolved_config, "max_completion_tokens", 1400),
            default_evaluator_max_completion_tokens=getattr(
                resolved_config, "evaluator_max_completion_tokens", 800
            ),
        )

        # Use mode presets if values are at their defaults, otherwise use explicit values
        api_retrieve_k = parsed_payload.retrieve_k
        api_controls_context_cap = parsed_payload.controls_context_cap
        api_temperature = parsed_payload.temperature
        api_top_p = getattr(parsed_payload, "top_p", 1.0)
        if api_top_p is None:
            api_top_p = 1.0
        if api_retrieve_k == 5 and api_thinking_mode:
            api_retrieve_k = int(api_mode_defaults.get("search_top_k", 5))
        if api_controls_context_cap is None:
            api_controls_context_cap = int(
                api_mode_defaults.get(
                    "controls_top_k", getattr(resolved_config, "controls_top_k", 4)
                )
            )
        if api_temperature == 1.0 and api_thinking_mode:
            api_temperature = float(api_mode_defaults.get("default_temperature", 1.0))
        if api_top_p == 1.0 and api_thinking_mode:
            api_top_p = float(
                api_mode_defaults.get("top_p", getattr(resolved_config, "top_p", 1.0))
            )

        required_dependencies = [
            resolved_is_authorised_request,
            resolved_unauthorised_message,
            resolved_run_rag,
            resolved_config,
            resolved_normalise_framework_filter,
            resolved_normalise_controls_comparison_mode,
            resolved_normalise_evidence_corpora,
            resolved_internal_error_message,
        ]
        if _has_missing_dependencies(required_dependencies):
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
                retrieve_k=api_retrieve_k,
                controls_context_cap=max(1, min(2000, int(api_controls_context_cap))),
                temperature=api_temperature,
                top_p=api_top_p,
                max_completion_tokens=getattr(parsed_payload, "max_completion_tokens", None)
                or api_mode_defaults.get("max_completion_tokens"),
                evaluator_max_completion_tokens=getattr(
                    parsed_payload, "evaluator_max_completion_tokens", None
                )
                or api_mode_defaults.get("evaluator_max_completion_tokens"),
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
            logger.exception(
                "Failed to process ask request",
                extra={
                    "event": "ask_failed",
                    "endpoint": "/api/ask",
                    "exc_type": type(exc).__name__,
                },
            )
            return ask_response_model(
                answer="",
                results=[],
                controls_results=[],
                controls_debug=None,
                evaluation=None,
                iterations=None,
                metrics=None,
                audit=None,
                error=_user_visible_ask_error(resolved_internal_error_message, exc),
            )
