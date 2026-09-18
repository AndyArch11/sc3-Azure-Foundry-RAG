"""Ask endpoint registration."""

# The endpoint layer intentionally delegates into `svc` helper methods that are
# named with leading underscores in `query_web.app` for backward compatibility.
# pylint: disable=protected-access,broad-exception-caught,too-many-positional-arguments

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from query_web.config import (
    _normalise_thinking_mode,
    _thinking_defaults,
    _thinking_mode_presets_for_ui,
)
from query_web.endpoints.problem_details import problem_response as _problem_response
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


def _coerce_positive_int(value: Any) -> int | None:
    """Convert a value to a positive int when possible.

    Args:
        value: Candidate value from env/config/payload.

    Returns:
        Positive integer or None when conversion fails.
    """
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_context_window_hint() -> tuple[int | None, str]:
    """Resolve an advisory context-window hint from model/provider/runtime metadata.

    Returns:
        A tuple of (hint_value, source).
    """
    sources: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "model_metadata",
            (
                "MODEL_MAX_POSITION_EMBEDDINGS",
                "MODEL_MAX_CONTEXT_TOKENS",
                "QUERY_MODEL_MAX_CONTEXT_TOKENS",
            ),
        ),
        (
            "provider_runtime",
            (
                "AZURE_OPENAI_MAX_CONTEXT_TOKENS",
                "OPENAI_MAX_CONTEXT_TOKENS",
                "BEDROCK_MAX_CONTEXT_TOKENS",
            ),
        ),
        (
            "runtime_config",
            (
                "LLM_CONTEXT_WINDOW_TOKENS",
                "CONTEXT_WINDOW_TOKENS",
                "OLLAMA_NUM_CTX",
            ),
        ),
    )
    for source, names in sources:
        for name in names:
            hint = _coerce_positive_int(os.getenv(name))
            if hint is not None:
                return (hint, source)
    return (None, "unknown")


def _model_capability_int(
    capabilities: dict[str, Any] | None,
    keys: tuple[str, ...],
) -> int | None:
    """Extract a positive integer model capability from a capability payload.

    Args:
        capabilities: Dictionary containing model capabilities.
        keys: Tuple of keys to look for in the capabilities dictionary.

    Returns:
        Positive integer value if found, otherwise None.
    """
    if not isinstance(capabilities, dict):
        return None
    for key in keys:
        value = _coerce_positive_int(capabilities.get(key))
        if value is not None:
            return value
    return None


def _resolve_request_timeout_hint_seconds() -> int | None:
    """Resolve an advisory request-timeout hint in seconds from runtime settings.

    Returns:
        Positive integer value if found, otherwise None.
    """
    for name in (
        "QUERY_WEB_ASK_TIMEOUT_S",
        "LLM_CHAT_TIMEOUT_S",
        "OLLAMA_CHAT_TIMEOUT",
    ):
        hint = _coerce_positive_int(os.getenv(name))
        if hint is not None:
            return hint
    return None


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
    resolve_query_model_capabilities: Any | None = None,
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
        is_authorised_request: Optional function to check request authorisdation.
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
        resolve_query_model_capabilities: Optional function that resolves provider/model capability metadata.
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
        include_graph_expansion: str = Form(""),
        graph_expansion_depth: int = Form(0),
        graph_expansion_max_edges: int = Form(0),
        evidence_corpora_include: list[str] = Form(default=[]),
        evidence_corpora_exclude: list[str] = Form(default=[]),
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
            controls_context_cap: The maximum context capacity for controls. Mileage may vary based on hardware and model size, but values > ~700 cause the model to return invalid JSON. The default is 0, which uses the model's default context size.
            temperature: The temperature setting for the model.
            top_p: The top-p setting for the model.
            max_completion_tokens: The maximum number of tokens for completion.
            evaluator_max_completion_tokens: The maximum number of tokens for the evaluator.
            controls_semantic: The semantic controls setting.
            controls_framework: The framework controls setting.
            controls_comparison_mode: The comparison mode for controls.
            include_graph_expansion: Whether graph neighbour expansion is enabled.
            graph_expansion_depth: Optional graph neighbour expansion depth.
            graph_expansion_max_edges: Optional graph neighbour edge budget.
            evidence_corpora_include: The list of evidence corpora to include.
            evidence_corpora_exclude: The list of evidence corpora to exclude.
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
                    "include_graph_expansion": resolved_form_bool(
                        include_graph_expansion, default=False
                    ),
                    "graph_expansion_depth": (
                        graph_expansion_depth if graph_expansion_depth > 0 else ""
                    ),
                    "graph_expansion_max_edges": (
                        graph_expansion_max_edges if graph_expansion_max_edges > 0 else ""
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
        controls_context_cap = max(1, min(3000, controls_context_cap))
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
        include_graph_expansion_enabled = resolved_form_bool(include_graph_expansion, default=False)
        graph_expansion_depth_value = (
            max(1, min(4, graph_expansion_depth)) if graph_expansion_depth > 0 else None
        )
        graph_expansion_max_edges_value = (
            max(1, min(200, graph_expansion_max_edges)) if graph_expansion_max_edges > 0 else None
        )
        evidence_corpora_include_filter = (
            resolved_normalise_evidence_corpora(evidence_corpora_include)
            if evidence_corpora_include
            else None
        )
        if evidence_corpora_include and not evidence_corpora_include_filter:
            # Backward compatibility for stale clients submitting unsupported
            # corpus values (for example legacy). Fall back to default scope.
            evidence_corpora_include_filter = None
        evidence_corpora_exclude_filter = (
            resolved_normalise_evidence_corpora(evidence_corpora_exclude)
            if evidence_corpora_exclude
            else None
        )

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
                include_graph_expansion=include_graph_expansion_enabled,
                graph_expansion_depth=graph_expansion_depth_value,
                graph_expansion_max_edges=graph_expansion_max_edges_value,
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
                "include_graph_expansion": include_graph_expansion_enabled,
                "graph_expansion_depth": graph_expansion_depth_value,
                "graph_expansion_max_edges": graph_expansion_max_edges_value,
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
        resolved_resolve_query_model_capabilities = _dep(
            "_resolve_query_model_capabilities", resolve_query_model_capabilities
        )
        resolved_internal_error_message = (
            internal_error_message
            if internal_error_message is not None
            else _dep("_INTERNAL_ERROR_MESSAGE", None)
        )

        try:
            parsed_payload = ask_request_model.model_validate(payload)
        except ValidationError as exc:
            logger.info(
                "Invalid ask API payload",
                extra={
                    "event": "ask_invalid_payload",
                    "endpoint": "/api/ask",
                    "errors": exc.errors(),
                },
            )
            return _problem_response(
                status=422,
                title="Unprocessable Content",
                detail="Invalid request payload.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.21",
                extensions={"errors": exc.errors()},
            )
        question = parsed_payload.question.strip()
        if not question:
            return _problem_response(
                status=422,
                title="Unprocessable Content",
                detail="Question must not be empty.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.21",
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

        api_max_completion_tokens = getattr(
            parsed_payload, "max_completion_tokens", None
        ) or api_mode_defaults.get("max_completion_tokens")
        api_evaluator_max_completion_tokens = getattr(
            parsed_payload, "evaluator_max_completion_tokens", None
        ) or api_mode_defaults.get("evaluator_max_completion_tokens")

        if payload.get("max_completion_tokens") is not None:
            max_completion_tokens_source = "request_override"
        elif payload.get("thinking_mode") is not None:
            max_completion_tokens_source = "thinking_mode_preset"
        else:
            max_completion_tokens_source = "runtime_config"

        model_capabilities: dict[str, Any] | None = None
        if callable(resolved_resolve_query_model_capabilities):
            try:
                resolved_caps = resolved_resolve_query_model_capabilities()
                if isinstance(resolved_caps, dict):
                    model_capabilities = resolved_caps
            except Exception:
                logger.debug("Model capability probe failed", exc_info=True)

        model_context_hint = _model_capability_int(
            model_capabilities,
            (
                "context_window_tokens",
                "max_position_embeddings",
                "max_context_tokens",
            ),
        )
        model_completion_cap = _model_capability_int(
            model_capabilities,
            (
                "max_output_tokens",
                "max_completion_tokens",
                "output_token_limit",
            ),
        )
        model_capability_source = str((model_capabilities or {}).get("source") or "model_metadata")

        api_max_completion_tokens_effective = _coerce_positive_int(api_max_completion_tokens)
        if model_completion_cap is not None and api_max_completion_tokens_effective is not None:
            api_max_completion_tokens_effective = min(
                api_max_completion_tokens_effective, model_completion_cap
            )
            max_completion_tokens_source = model_capability_source

        api_evaluator_max_completion_tokens_effective = _coerce_positive_int(
            api_evaluator_max_completion_tokens
        )
        if (
            model_completion_cap is not None
            and api_evaluator_max_completion_tokens_effective is not None
        ):
            api_evaluator_max_completion_tokens_effective = min(
                api_evaluator_max_completion_tokens_effective,
                model_completion_cap,
            )

        context_window_tokens_hint = model_context_hint
        context_window_source = (
            model_capability_source if model_context_hint is not None else "unknown"
        )
        if context_window_tokens_hint is None:
            context_window_tokens_hint, context_window_source = _resolve_context_window_hint()
        runtime_hints = {
            "context_window_tokens_hint": context_window_tokens_hint,
            "context_window_source": context_window_source,
            "max_completion_tokens_effective": api_max_completion_tokens_effective,
            "max_completion_tokens_source": max_completion_tokens_source,
            "evaluator_max_completion_tokens_effective": api_evaluator_max_completion_tokens_effective,
            "request_timeout_seconds_hint": _resolve_request_timeout_hint_seconds(),
            "provider_constraints_note": (
                "Runtime limits vary by provider/model and deployment configuration. "
                "Treat hints as advisory."
            ),
        }

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
            return _problem_response(
                status=500,
                title="Internal Server Error",
                detail="Ask API endpoint misconfigured.",
                instance=str(request.url.path),
            )

        if not resolved_is_authorised_request(parsed_payload.auth_token, request):
            return _problem_response(
                status=401,
                title="Unauthorized",
                detail=resolved_unauthorised_message(request),
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.2",
            )

        api_evidence_corpora_exclude = resolved_normalise_evidence_corpora(
            parsed_payload.evidence_corpora_exclude or []
        )
        api_include_raw = parsed_payload.evidence_corpora_include
        api_evidence_corpora_include: list[Any] | None = None
        include_none_warning: str | None = None
        if api_include_raw is not None:
            # Preserve explicit include semantics when provided.
            if len(api_include_raw) == 0:
                api_evidence_corpora_include = []
                include_none_warning = (
                    "No evidence corpora selected for retrieval. "
                    "Provide evidence_corpora_include with one or more of: a, b, c."
                )
            else:
                normalised_include = resolved_normalise_evidence_corpora(api_include_raw) or []
                if not normalised_include:
                    # Unsupported/stale corpus values should not force an
                    # empty-scope answer; use default corpus scope instead.
                    api_evidence_corpora_include = None
                    include_none_warning = (
                        "Unsupported evidence_corpora_include values were ignored. "
                        "Using default corpora: a, b, c."
                    )
                else:
                    api_evidence_corpora_include = normalised_include

        try:
            result = resolved_run_rag(
                question=question,
                retrieve_k=api_retrieve_k,
                controls_context_cap=max(1, min(3000, int(api_controls_context_cap))),
                temperature=api_temperature,
                top_p=api_top_p,
                max_completion_tokens=api_max_completion_tokens_effective,
                evaluator_max_completion_tokens=api_evaluator_max_completion_tokens_effective,
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
                include_graph_expansion=bool(
                    getattr(parsed_payload, "include_graph_expansion", False)
                ),
                graph_expansion_depth=getattr(parsed_payload, "graph_expansion_depth", None),
                graph_expansion_max_edges=getattr(
                    parsed_payload, "graph_expansion_max_edges", None
                ),
                evidence_corpora_include=api_evidence_corpora_include,
                evidence_corpora_exclude=api_evidence_corpora_exclude,
            )
            audit_payload = dict(result.get("audit") or {})
            if include_none_warning:
                warnings = list(audit_payload.get("warnings") or [])
                warnings.append(include_none_warning)
                audit_payload["warnings"] = warnings
            return ask_response_model(
                answer=result["answer"],
                results=result["results"],
                controls_results=result.get("controls_results", []),
                controls_debug=result.get("controls_debug"),
                evaluation=result["evaluation"],
                iterations=result["iterations"],
                metrics=result["metrics"],
                graph_capabilities=result.get("graph_capabilities"),
                graph_summary=result.get("graph_summary"),
                community_summaries=result.get("community_summaries"),
                corpus_a_entities=result.get("corpus_a_entities"),
                corpus_b_entities=result.get("corpus_b_entities"),
                graph_links=result.get("graph_links"),
                runtime_hints=runtime_hints,
                audit=audit_payload,
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
            return _problem_response(
                status=500,
                title="Internal Server Error",
                detail=_user_visible_ask_error(resolved_internal_error_message, exc),
                instance=str(request.url.path),
            )
