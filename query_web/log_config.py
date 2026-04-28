"""Structured JSON logging configuration for query-web.

This module is a thin adapter over ``runtime.log_config`` (the canonical
implementation).  The only difference from the runtime variant is the
``context_getter``: instead of reading raw contextvars, it delegates to the
**public** accessor functions in ``query_web.request_context`` —
``get_correlation_id``, ``get_traceparent``, and ``get_tracestate`` — which
act as the authoritative guardrails for correlation/trace values:

* ``get_correlation_id``: strips log-unsafe characters, caps at 128 chars
  (CRLF/log-injection guard).
* ``get_traceparent``: W3C Trace Context §3.2 validation; rejects reserved
  versions and all-zero IDs; salvages unknown future versions as v00.
* ``get_tracestate``: capped at 512 chars; cleared when traceparent is absent.

All redaction helpers, ``JsonFormatter``, ``CorrelationContextFilter``, and
``_LOG_RECORD_BUILT_INS`` are re-exported directly from ``runtime.log_config``
so there is a single source of truth.

Usage::

    from query_web.log_config import configure_logging
    configure_logging("query-web")
"""

from __future__ import annotations

import logging

from runtime.log_config import (
    _EXTRA_FIELD_ALLOWLIST,
    _LOG_RECORD_BUILT_INS,
    _MAX_FIELD_VALUE_LEN,
    _SENSITIVE_KEYS,
    CorrelationContextFilter,
    JsonFormatter,
    _redact_dict,
    _redact_str,
    _redact_value,
)
from runtime.log_config import (
    configure_logging as _configure_logging_base,  # re-export for callers and tests
)

__all__ = [
    "configure_logging",
    "CorrelationContextFilter",
    "_EXTRA_FIELD_ALLOWLIST",
    "JsonFormatter",
    "_LOG_RECORD_BUILT_INS",
    "_MAX_FIELD_VALUE_LEN",
    "_SENSITIVE_KEYS",
    "_redact_dict",
    "_redact_str",
    "_redact_value",
]


def _query_web_context_getter() -> tuple[str, str, str]:
    """Return sanitised correlation/trace IDs via query_web.request_context.

    Delegates to the public getter functions which enforce W3C validation,
    CRLF stripping, and length caps — the same guards applied to inbound
    HTTP headers by the request-context middleware.
    """
    from query_web.request_context import (  # noqa: PLC0415
        get_correlation_id,
        get_traceparent,
        get_tracestate,
    )

    return get_correlation_id(), get_traceparent(), get_tracestate()


def configure_logging(service: str = "query-web", level: int | str = logging.INFO) -> None:
    """Configure structured JSON logging for the query-web service.

    Delegates to ``runtime.log_config.configure_logging`` with the query-web
    context getter so that every log record carries correlation/trace IDs
    sourced through ``query_web.request_context``'s validated accessors.

    Safe to call multiple times; idempotent (replaces StreamHandlers on each
    call, no duplicates).

    Args:
        service: Value emitted as the ``service`` field on every record.
        level:   Minimum log level for the root logger.
    """
    _configure_logging_base(service=service, level=level, context_getter=_query_web_context_getter)
