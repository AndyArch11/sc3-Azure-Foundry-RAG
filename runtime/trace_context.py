"""
    Trace context management for cross-service correlation propagation.

    This module provides utilities for managing W3C Trace Context headers
    (`traceparent` and `tracestate`) and a custom `x-correlation-id` header
    within the runtime. It includes functions for setting, resetting, and
    scoping trace context, as well as building outbound headers for HTTP requests.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar, Token

# ---------------------------------------------------------------------------
# Guards: max inbound header lengths (DoS / log-bloat protection)
# ---------------------------------------------------------------------------
_MAX_CORRELATION_ID_LEN = 128
_MAX_TRACESTATE_LEN = 512

# ---------------------------------------------------------------------------
# W3C Trace Context §3.2 — traceparent validation
# ---------------------------------------------------------------------------
_TRACEPARENT_V00_RE = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)
_TRACEPARENT_FUTURE_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})"
)
_CORRELATION_ID_SAFE_RE = re.compile(r"[^a-zA-Z0-9.\-_]")

_CORRELATION_ID: ContextVar[str] = ContextVar("runtime_correlation_id", default="")
_TRACEPARENT: ContextVar[str] = ContextVar("runtime_traceparent", default="")
_TRACESTATE: ContextVar[str] = ContextVar("runtime_tracestate", default="")


def _clean(value: str | None) -> str:
    """Sanitise a string value for trace context headers.

    Args:
        value: The string value to sanitise.
    
    Returns:
        The sanitised string value, or an empty string if the input was None.
    """
    return (value or "").strip()


def _sanitise_correlation_id(value: str) -> str:
    """Strip log-unsafe characters and cap length (log injection / DoS guard).

    Args:
        value: The correlation ID to sanitise.

    Returns:
        The sanitised correlation ID.
    """
    return _CORRELATION_ID_SAFE_RE.sub("", value)[:_MAX_CORRELATION_ID_LEN]


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
        value: The traceparent header value to validate.

    Returns:
        The validated traceparent header value, or an empty string if invalid.
    """
    s = _clean(value).lower()
    if not s or len(s) < 2:
        return ""
    version = s[:2]
    if version == "ff":
        return ""
    m00 = _TRACEPARENT_V00_RE.match(s)
    if m00:
        trace_id, parent_id = m00.group(1), m00.group(2)
        if trace_id == "0" * 32 or parent_id == "0" * 16:
            return ""
        return s
    mf = _TRACEPARENT_FUTURE_RE.match(s)
    if mf:
        trace_id, parent_id, flags = mf.group(2), mf.group(3), mf.group(4)
        if trace_id != "0" * 32 and parent_id != "0" * 16:
            return f"00-{trace_id}-{parent_id}-{flags}"
    return ""


def _sanitise_tracestate(value: str) -> str:
    """Cap tracestate at the spec's 512-character maximum (DoS / log-bloat guard).

    Args:
        value: The tracestate header value to sanitise.

    Returns:
        The sanitised tracestate header value.
    """
    return _clean(value)[:_MAX_TRACESTATE_LEN]


def set_trace_context(
    *,
    correlation_id: str = "",
    traceparent: str = "",
    tracestate: str = "",
) -> tuple[Token[str], Token[str], Token[str]]:
    return (
        _CORRELATION_ID.set(_sanitise_correlation_id(correlation_id)),
        _TRACEPARENT.set(_validate_traceparent(traceparent)),
        _TRACESTATE.set(_sanitise_tracestate(tracestate)),
    )


def reset_trace_context(tokens: tuple[Token[str], Token[str], Token[str]]) -> None:
    """Reset the trace context to the previous state using the provided tokens.

    Args:
        tokens: A tuple of tokens returned by `set_trace_context`.
    """
    correlation_token, traceparent_token, tracestate_token = tokens
    _CORRELATION_ID.reset(correlation_token)
    _TRACEPARENT.reset(traceparent_token)
    _TRACESTATE.reset(tracestate_token)


@contextmanager
def scoped_trace_context(
    *, correlation_id: str = "", traceparent: str = "", tracestate: str = ""
):
    """Context manager for temporarily setting trace context headers.

    Args:
        correlation_id: The correlation ID to set.
        traceparent: The traceparent header value to set.
        tracestate: The tracestate header value to set.

    Yields:
        None
    """
    tokens = set_trace_context(
        correlation_id=correlation_id,
        traceparent=traceparent,
        tracestate=tracestate,
    )
    try:
        yield
    finally:
        reset_trace_context(tokens)


def outbound_trace_headers(
    *,
    correlation_id: str | None = None,
    traceparent: str | None = None,
    tracestate: str | None = None,
) -> dict[str, str]:
    """Build outbound headers for cross-service correlation propagation.

    Includes tracestate alongside traceparent per W3C Trace Context spec §3.3.

    Args:
        correlation_id: Optional correlation ID to propagate. If not provided, the current context value is used.
        traceparent: Optional traceparent header value to propagate. If not provided, the current context value is used.
        tracestate: Optional tracestate header value to propagate. If not provided, the current context value is used.

    Returns:
        A dictionary of headers to include in outbound HTTP requests for trace propagation.
    """
    corr = _clean(correlation_id) or _clean(_CORRELATION_ID.get())
    tp = _clean(traceparent) or _clean(_TRACEPARENT.get())
    ts = _clean(tracestate) or _clean(_TRACESTATE.get())
    headers: dict[str, str] = {}
    if corr:
        headers["x-correlation-id"] = corr
    if tp:
        headers["traceparent"] = tp
        if ts:
            headers["tracestate"] = ts
    return headers
