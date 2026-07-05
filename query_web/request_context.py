"""Request correlation/trace context helpers for query-web."""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import Token

from fastapi import FastAPI, Request
from starlette.responses import Response

from runtime.trace_context import (  # shared pure logic, no Prometheus side effects
    _CORRELATION_ID_SAFE_RE,
    _MAX_CORRELATION_ID_LEN,
    _MAX_TRACESTATE_LEN,
    _TRACEPARENT_FUTURE_RE,
    _TRACEPARENT_V00_RE,
    _clean,
    _sanitise_tracestate,
)

CORRELATION_ID_HEADER = "x-correlation-id"
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"

_CORRELATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "query_web_correlation_id", default=""
)
_TRACEPARENT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "query_web_traceparent", default=""
)
_TRACESTATE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "query_web_tracestate", default=""
)


def _new_correlation_id() -> str:
    """Generate a new random correlation ID (32 hex chars).

    Returns:
        A new correlation ID string.
    """
    return uuid.uuid4().hex


def _sanitise_correlation_id(value: str) -> str:
    """Strip log-unsafe characters and cap length (log injection / DoS guard).

    Args:
        value: The raw correlation ID value to sanitise.
    Returns:
        The sanitised correlation ID string.
    """
    sanitised = _CORRELATION_ID_SAFE_RE.sub("", value)[:_MAX_CORRELATION_ID_LEN]
    if sanitised != value[:_MAX_CORRELATION_ID_LEN]:
        from query_web.metrics import CORRELATION_ID_SANITISED_TOTAL  # noqa: PLC0415

        CORRELATION_ID_SANITISED_TOTAL.inc()
    return sanitised


def _validate_traceparent(value: str) -> str:
    """Validate traceparent per W3C Trace Context spec §3.2.

    - Version ``00``: fully validated; all-zero trace-id or parent-id → dropped.
    - Unknown future versions (``01``-``fe``): attempt field extraction and
      re-emit as a version-00 header so downstream vendors remain compatible.
    - Version ``ff``: reserved; always dropped.
    - Malformed input: dropped (returns empty string).

    The sampled flag (bit 0 of trace-flags) is propagated faithfully without
    behavioural change — sampling decisions are the vendor's responsibility.

    Args:
        value: The raw traceparent header value to validate.
    Returns:
        The validated traceparent string, or an empty string if invalid.
    """
    s = (value or "").strip().lower()
    if not s:
        return ""
    if len(s) < 2:
        return ""

    version = s[:2]

    # ff is explicitly reserved by the spec
    if version == "ff":
        from query_web.metrics import TRACE_DROPPED_TOTAL  # noqa: PLC0415

        TRACE_DROPPED_TOTAL.labels(reason="reserved_version").inc()
        return ""

    # Current stable version — full strict validation
    m00 = _TRACEPARENT_V00_RE.match(s)
    if m00:
        trace_id, parent_id = m00.group(1), m00.group(2)
        if trace_id == "0" * 32 or parent_id == "0" * 16:
            from query_web.metrics import TRACE_DROPPED_TOTAL  # noqa: PLC0415

            TRACE_DROPPED_TOTAL.labels(reason="all_zeros").inc()
            return ""
        return s

    # Unknown future version: salvage per spec recommendation
    mf = _TRACEPARENT_FUTURE_RE.match(s)
    if mf:
        trace_id, parent_id, flags = mf.group(2), mf.group(3), mf.group(4)
        if trace_id != "0" * 32 and parent_id != "0" * 16:
            from query_web.metrics import TRACE_SALVAGED_TOTAL  # noqa: PLC0415

            TRACE_SALVAGED_TOTAL.inc()
            # Re-emit as v00 so downstream vendors always receive a known format
            return f"00-{trace_id}-{parent_id}-{flags}"

    from query_web.metrics import TRACE_DROPPED_TOTAL  # noqa: PLC0415

    TRACE_DROPPED_TOTAL.labels(reason="malformed").inc()
    return ""


def resolve_request_ids(request: Request) -> tuple[str, str, str]:
    """Resolve incoming request IDs, generating a correlation ID when absent.

    Args:
        request: The FastAPI Request object from which to extract headers.

    Returns:
        A tuple containing (correlation_id, traceparent, tracestate).
    """
    raw_corr = _clean(request.headers.get(CORRELATION_ID_HEADER))
    correlation_id = _sanitise_correlation_id(raw_corr) if raw_corr else _new_correlation_id()
    traceparent = _validate_traceparent(request.headers.get(TRACEPARENT_HEADER, ""))
    tracestate = _sanitise_tracestate(request.headers.get(TRACESTATE_HEADER, ""))
    # tracestate is only meaningful alongside a valid traceparent
    if not traceparent:
        tracestate = ""
    return correlation_id, traceparent, tracestate


def set_request_context(
    *, correlation_id: str, traceparent: str, tracestate: str = ""
) -> tuple[Token[str], Token[str], Token[str]]:
    """Set request-local contextvars and return tokens for reset.

    Args:
        correlation_id: The correlation ID to set in the context.
        traceparent: The traceparent value to set in the context.
        tracestate: The tracestate value to set in the context (default is empty).

    Returns:
        A tuple of tokens for resetting the contextvars.
    """
    return (
        _CORRELATION_ID.set(correlation_id),
        _TRACEPARENT.set(traceparent),
        _TRACESTATE.set(tracestate),
    )


def reset_request_context(tokens: tuple[Token[str], Token[str], Token[str]]) -> None:
    """Reset request-local contextvars using tokens from set_request_context.

    Args:
        tokens: A tuple of tokens returned by set_request_context.
    """
    correlation_token, traceparent_token, tracestate_token = tokens
    _CORRELATION_ID.reset(correlation_token)
    _TRACEPARENT.reset(traceparent_token)
    _TRACESTATE.reset(tracestate_token)


def get_correlation_id(request: Request | None = None) -> str:
    """Return correlation_id from request state/header, then contextvar fallback.

    Args:
        request: The FastAPI Request object from which to extract the correlation ID (optional).

    Returns:
        The correlation ID string.
    """
    if request is not None:
        state_value = getattr(request.state, "correlation_id", "")
        if isinstance(state_value, str) and state_value.strip():
            return state_value.strip()
        header_value = _clean(request.headers.get(CORRELATION_ID_HEADER))
        if header_value:
            return header_value
    return _clean(_CORRELATION_ID.get())


def get_traceparent(request: Request | None = None) -> str:
    """Return traceparent from request state/header, then contextvar fallback.

    Args:
        request: The FastAPI Request object from which to extract the traceparent (optional).

    Returns:
        The traceparent string.
    """
    if request is not None:
        state_value = getattr(request.state, "traceparent", "")
        if isinstance(state_value, str) and state_value.strip():
            return state_value.strip()
        header_value = _clean(request.headers.get(TRACEPARENT_HEADER))
        if header_value:
            return header_value
    return _clean(_TRACEPARENT.get())


def get_tracestate(request: Request | None = None) -> str:
    """Return tracestate from request state/header, then contextvar fallback.

    Args:
        request: The FastAPI Request object from which to extract the tracestate (optional).

    Returns:
        The tracestate string.
    """
    if request is not None:
        state_value = getattr(request.state, "tracestate", "")
        if isinstance(state_value, str) and state_value.strip():
            return state_value.strip()
        header_value = _clean(request.headers.get(TRACESTATE_HEADER))
        if header_value:
            return header_value
    return _clean(_TRACESTATE.get())


def outbound_trace_headers(
    *,
    correlation_id: str | None = None,
    traceparent: str | None = None,
    tracestate: str | None = None,
) -> dict[str, str]:
    """Build outbound headers for cross-service correlation propagation.

    Includes tracestate alongside traceparent per W3C Trace Context spec §3.3.

    Args:
        correlation_id: Optional correlation ID to include in the headers.
        traceparent: Optional traceparent value to include in the headers.
        tracestate: Optional tracestate value to include in the headers.

    Returns:
        A dictionary of headers to include in outbound requests for correlation propagation.
    """
    corr = _clean(correlation_id) or _clean(_CORRELATION_ID.get())
    tp = _clean(traceparent) or _clean(_TRACEPARENT.get())
    ts = _clean(tracestate) or _clean(_TRACESTATE.get())
    headers: dict[str, str] = {}
    if corr:
        headers[CORRELATION_ID_HEADER] = corr
    if tp:
        headers[TRACEPARENT_HEADER] = tp
        if ts:
            headers[TRACESTATE_HEADER] = ts
    return headers


def register_request_context_middleware(app: FastAPI) -> None:
    """Attach middleware that resolves and propagates correlation/trace IDs.

    Args:
        app: The FastAPI application instance to which the middleware will be added.
    """

    @app.middleware("http")
    async def _request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id, traceparent, tracestate = resolve_request_ids(request)
        request.state.correlation_id = correlation_id
        request.state.traceparent = traceparent
        request.state.tracestate = tracestate

        tokens = set_request_context(
            correlation_id=correlation_id,
            traceparent=traceparent,
            tracestate=tracestate,
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed = time.perf_counter() - started
            reset_request_context(tokens)

        from query_web.metrics import HTTP_REQUEST_DURATION  # noqa: PLC0415

        path = request.url.path
        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            path=path,
            status=str(response.status_code),
        ).observe(elapsed)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        if traceparent:
            response.headers[TRACEPARENT_HEADER] = traceparent
        if tracestate:
            response.headers[TRACESTATE_HEADER] = tracestate
        return response
