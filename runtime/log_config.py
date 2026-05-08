"""Canonical structured JSON logging used by both query-web and runtime workers.

Call ``configure_logging(service=..., context_getter=...)`` once at startup:

* ``JsonFormatter`` — emits one JSON object per record with a stable schema:
  ``timestamp``, ``level``, ``logger``, ``service``, ``message``,
  ``correlation_id``, ``traceparent``, and optionally ``tracestate``.
* ``CorrelationContextFilter`` — accepts a ``context_getter`` callable that
  returns ``(correlation_id, traceparent, tracestate)`` and injects those
  values into every record.  Callers who set values via ``extra={}`` take
  precedence; the getter is only a fallback.
* Redaction of sensitive keys and bearer/basic-auth token patterns before
  records leave the process.

Two pre-built context getters are provided:

* ``runtime_context_getter()`` — reads from ``runtime.trace_context``
  contextvars and applies the sanitisation helpers from that module.
* The query-web getter lives in ``query_web.log_config`` and delegates to
  the public ``get_*`` accessors in ``query_web.request_context`` which
  enforce W3C traceparent validation, CRLF stripping, and length caps.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_REDACTED = "***REDACTED***"

#: Keys whose values are always redacted, regardless of capitalisation.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "authorisation",
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "cookie",
        "set-cookie",
        "x-api-key",
        "client_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "sas_token",
        "connection_string",
        "private_key",
        "key_material",
    }
)

_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{10,}")
_BASIC_RE = re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]{6,}")
# Bare JWT: ey<header>.<payload>.<signature>  (base64url, 3 segments, no bearer prefix required)
_JWT_RE = re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def _redact_str(value: str) -> str:
    value = _BEARER_RE.sub(r"\1" + _REDACTED, value)
    value = _BASIC_RE.sub(r"\1" + _REDACTED, value)
    value = _JWT_RE.sub(_REDACTED, value)
    return value


def _redact_value(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        return _redact_dict(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
            out[k] = _REDACTED
        else:
            out[k] = _redact_value(v)
    return out


# ---------------------------------------------------------------------------
# Extra-field allowlist and value truncation
# ---------------------------------------------------------------------------

_MAX_FIELD_VALUE_LEN: int = 512
"""Maximum character length for a string value in a structured-log extra field.

Values exceeding this are replaced with the first ``_MAX_FIELD_VALUE_LEN``
characters followed by the suffix ``…[truncated]``.
"""

#: Only field names in this set are emitted in structured extras.
#: Fields added by third-party libraries (e.g. ``color_message``, ``markup``,
#: ``rich``) are silently dropped, preventing both log bloat and accidental
#: leakage of library-internal state.
#:
#: Sensitive key names are intentionally included here: they are allowed
#: through the allowlist but their *values* are always redacted by
#: ``_redact_dict``.
_EXTRA_FIELD_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Correlation / trace (also injected by CorrelationContextFilter)
        "correlation_id",
        "traceparent",
        "tracestate",
        # Event identity
        "event",
        # Error context
        "exc_type",
        "error_code",
        "reason",
        # Request / HTTP context
        "endpoint",
        "route",
        "method",
        "status",
        "status_code",
        # Business / resource context
        "corpus",
        "framework",
        "index_name",
        "indexer_name",
        "data_source",
        "container",
        "operation",
        "mode",
        "provider",
        "model",
        "deployment",
        "component",
        "search_endpoint",
        # Identifiers
        "user_id",
        "session_id",
        "conversation_id",
        "job_id",
        "request_id",
        "upload_batch",
        "prefix",
        "content_id",
        # Metrics / telemetry
        "count",
        "total",
        "duration_ms",
        "elapsed",
        "attempt",
        "retry_count",
        # Flags / direction
        "dry_run",
        "direction",
        "system",
        "target",
        "source",
        # Sensitive keys: allowed through so their redacted representation is
        # visible in logs; _redact_dict replaces the actual values.
        "authorization",
        "authorisation",
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "cookie",
        "set-cookie",
        "x-api-key",
        "client_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "sas_token",
        "connection_string",
        "private_key",
        "key_material",
    }
)


# ---------------------------------------------------------------------------
# LogRecord built-in attribute names — skip when collecting extras
# ---------------------------------------------------------------------------

_LOG_RECORD_BUILT_INS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
        # Fields injected by CorrelationContextFilter
        "correlation_id",
        "traceparent",
        "tracestate",
    }
)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record with a stable schema."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        record.message = record.getMessage()

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "message": _redact_str(record.message),
        }

        # Correlation / trace fields — injected by CorrelationContextFilter
        cid: str = getattr(record, "correlation_id", "")
        if cid:
            payload["correlation_id"] = cid
        tp: str = getattr(record, "traceparent", "")
        if tp:
            payload["traceparent"] = tp
        ts: str = getattr(record, "tracestate", "")
        if ts:
            payload["tracestate"] = ts

        # Extra structured fields — filtered to allowlist, then redacted and truncated.
        # Fields not in _EXTRA_FIELD_ALLOWLIST are silently dropped to prevent
        # third-party library fields (color_message, markup, rich, …) from leaking.
        extras: dict[str, Any] = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _LOG_RECORD_BUILT_INS
            and not k.startswith("_")
            and k in _EXTRA_FIELD_ALLOWLIST
        }
        if extras:
            redacted = _redact_dict(extras)
            payload.update(
                {
                    k: v[:_MAX_FIELD_VALUE_LEN] + "…[truncated]"
                    if isinstance(v, str) and len(v) > _MAX_FIELD_VALUE_LEN
                    else v
                    for k, v in redacted.items()
                }
            )

        # Exception details — stack trace is redacted before emission so that
        # tokens or credentials that appear in exception messages cannot leak.
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
            payload["stack"] = _redact_str(self.formatException(record.exc_info))

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Context getter — runtime (built-in default)
# ---------------------------------------------------------------------------


def _runtime_context_getter() -> tuple[str, str, str]:
    """Read correlation/trace IDs from runtime.trace_context with sanitisation.

    Applies ``_sanitise_correlation_id`` and ``_sanitise_tracestate`` from
    ``runtime.trace_context`` so that values reaching log records go through
    the same guards as inbound header values.
    """
    try:
        from runtime.trace_context import (  # noqa: PLC0415
            _CORRELATION_ID,
            _TRACEPARENT,
            _TRACESTATE,
            _sanitise_correlation_id,
            _sanitise_tracestate,
        )
    except ModuleNotFoundError:
        # Runtime container image may copy modules into /app without the
        # top-level runtime package path.
        from trace_context import _CORRELATION_ID  # noqa: PLC0415  # type: ignore[no-redef]
        from trace_context import _TRACEPARENT  # noqa: PLC0415  # type: ignore[no-redef]
        from trace_context import _TRACESTATE  # noqa: PLC0415  # type: ignore[no-redef]
        from trace_context import _sanitise_correlation_id  # noqa: PLC0415  # type: ignore[no-redef]
        from trace_context import _sanitise_tracestate  # noqa: PLC0415  # type: ignore[no-redef]

    cid = _sanitise_correlation_id(_CORRELATION_ID.get())
    tp = _TRACEPARENT.get().strip()
    # tracestate is only meaningful alongside a valid traceparent
    ts = _sanitise_tracestate(_TRACESTATE.get()) if tp else ""
    return cid, tp, ts


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class CorrelationContextFilter(logging.Filter):
    """Inject correlation_id / traceparent / tracestate via a context getter.

    Args:
        context_getter: Callable that returns ``(correlation_id, traceparent,
            tracestate)`` for the current execution context.  Defaults to
            ``_runtime_context_getter`` which reads from
            ``runtime.trace_context`` contextvars.

    Values explicitly set by callers via ``extra={}`` take priority; the
    getter is only consulted when the record has no existing value.
    """

    def __init__(
        self,
        context_getter: Callable[[], tuple[str, str, str]] | None = None,
    ) -> None:
        super().__init__()
        self._getter = context_getter or _runtime_context_getter

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        cid, tp, ts = self._getter()
        if not getattr(record, "correlation_id", ""):
            record.correlation_id = cid
        if not getattr(record, "traceparent", ""):
            record.traceparent = tp
        if not getattr(record, "tracestate", ""):
            record.tracestate = ts
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(
    service: str = "runtime",
    level: int | str = logging.INFO,
    *,
    context_getter: Callable[[], tuple[str, str, str]] | None = None,
) -> None:
    """Configure structured JSON logging on the root logger.

    Safe to call multiple times; each call replaces existing StreamHandlers so
    there are no duplicate output lines.  Non-StreamHandler handlers (e.g.
    Azure Monitor queue handlers) are left in place.

    Args:
        service:        Value emitted as the ``service`` field on every record.
        level:          Minimum log level for the root logger.
        context_getter: Callable returning ``(correlation_id, traceparent,
                        tracestate)``.  Defaults to ``_runtime_context_getter``.
    """
    root = logging.getLogger()
    root.setLevel(level)

    root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)]

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    handler.addFilter(CorrelationContextFilter(context_getter))
    root.addHandler(handler)
