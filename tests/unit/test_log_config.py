"""Unit tests for query_web.log_config and runtime.log_config.

Covers:
- Every emitted log record contains correlation_id and traceparent fields.
- tracestate appears only when non-empty.
- Sensitive key values are redacted before emission.
- Bearer and Basic auth token patterns are redacted in message strings.
- Extra structured fields are carried through to the JSON output.
- query_web.log_config is a shim over runtime.log_config; no duplicate code.
- context_getter is pluggable: query-web uses request_context public accessors
  (W3C validation + sanitization guardrails); runtime uses trace_context with
  its own sanitization helpers.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from query_web.log_config import _EXTRA_FIELD_ALLOWLIST as _q_extra_field_allowlist
from query_web.log_config import _MAX_FIELD_VALUE_LEN as _q_max_field_value_len
from query_web.log_config import CorrelationContextFilter as QCorrelationContextFilter
from query_web.log_config import JsonFormatter as QJsonFormatter  # must be the same class
from query_web.log_config import (
    _query_web_context_getter,
)
from query_web.log_config import _redact_dict as _q_redact_dict
from query_web.log_config import _redact_str as _q_redact_str
from query_web.log_config import (
    configure_logging,
)

# All redaction/formatting symbols live in runtime.log_config (canonical).
# query_web.log_config re-exports them, so both import paths must work.
from runtime.log_config import (
    CorrelationContextFilter,
    JsonFormatter,
    _redact_dict,
    _redact_str,
    _redact_value,
)
from runtime.log_config import configure_logging as _runtime_configure_logging

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_logger(service: str = "test-svc") -> tuple[logging.Logger, StringIO]:
    """Return a logger + StringIO that captures JSON output."""
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter(service))
    log = logging.getLogger(f"test.log_config.{id(buf)}")
    log.handlers = []
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return log, buf


def _record(buf: StringIO) -> dict:
    """Parse the last JSON line written to buf."""
    buf.seek(0)
    lines = [ln for ln in buf.read().splitlines() if ln.strip()]
    assert lines, "No log output captured"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# JsonFormatter — stable schema fields
# ---------------------------------------------------------------------------


class TestJsonFormatterSchema:
    def test_standard_fields_present(self) -> None:
        log, buf = _capture_logger("my-service")
        log.info("hello world")
        r = _record(buf)

        assert r["level"] == "INFO"
        assert r["logger"].startswith("test.log_config.")
        assert r["service"] == "my-service"
        assert r["message"] == "hello world"
        assert "timestamp" in r
        # Timestamp must parse as ISO-8601
        from datetime import datetime

        datetime.fromisoformat(r["timestamp"])

    def test_extra_fields_carried_through(self) -> None:
        log, buf = _capture_logger()
        log.info(
            "cosmos_schema_access",
            extra={"correlation_id": "abc123", "operation": "read", "container": "state"},
        )
        r = _record(buf)

        assert r["operation"] == "read"
        assert r["container"] == "state"

    def test_exception_info_emitted(self) -> None:
        log, buf = _capture_logger()
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("something failed")

        r = _record(buf)
        assert r["error_type"] == "ValueError"
        assert "boom" in r["stack"]

    def test_debug_level_respected(self) -> None:
        log, buf = _capture_logger()
        log.debug("verbose detail")
        r = _record(buf)
        assert r["level"] == "DEBUG"


# ---------------------------------------------------------------------------
# Correlation fields via CorrelationContextFilter
# ---------------------------------------------------------------------------


class TestCorrelationContextFilter:
    def _make_filtered_logger(self) -> tuple[logging.Logger, StringIO]:
        """Build a logger with the query-web context getter (uses request_context public accessors)."""
        buf = StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonFormatter("svc"))
        handler.addFilter(CorrelationContextFilter(_query_web_context_getter))
        log = logging.getLogger(f"test.corr.{id(buf)}")
        log.handlers = []
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        return log, buf

    def test_correlation_id_injected_from_contextvar(self) -> None:
        from query_web.request_context import reset_request_context, set_request_context

        tokens = set_request_context(correlation_id="corr-xyz", traceparent="", tracestate="")
        try:
            log, buf = self._make_filtered_logger()
            log.info("test message")
            r = _record(buf)
            assert r["correlation_id"] == "corr-xyz"
        finally:
            reset_request_context(tokens)

    def test_traceparent_injected_from_contextvar(self) -> None:
        from query_web.request_context import reset_request_context, set_request_context

        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        tokens = set_request_context(correlation_id="c1", traceparent=tp, tracestate="")
        try:
            log, buf = self._make_filtered_logger()
            log.info("test message")
            r = _record(buf)
            assert r["traceparent"] == tp
        finally:
            reset_request_context(tokens)

    def test_tracestate_present_only_when_non_empty(self) -> None:
        from query_web.request_context import reset_request_context, set_request_context

        # Without tracestate
        tokens = set_request_context(correlation_id="c2", traceparent="", tracestate="")
        try:
            log, buf = self._make_filtered_logger()
            log.info("no tracestate")
            r = _record(buf)
            assert "tracestate" not in r
        finally:
            reset_request_context(tokens)

        # With tracestate
        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        tokens = set_request_context(correlation_id="c3", traceparent=tp, tracestate="vendor=val")
        try:
            log, buf = self._make_filtered_logger()
            log.info("with tracestate")
            r = _record(buf)
            assert r["tracestate"] == "vendor=val"
        finally:
            reset_request_context(tokens)

    def test_no_correlation_context_omits_fields(self) -> None:
        from query_web.request_context import _CORRELATION_ID, _TRACEPARENT, _TRACESTATE

        # Ensure contextvars are at their defaults (empty string)
        _CORRELATION_ID.set("")
        _TRACEPARENT.set("")
        _TRACESTATE.set("")

        log, buf = self._make_filtered_logger()
        log.info("bare message")
        r = _record(buf)

        assert "correlation_id" not in r
        assert "traceparent" not in r
        assert "tracestate" not in r

    def test_caller_extra_correlation_id_wins_over_contextvar(self) -> None:
        """Explicitly passed extra={"correlation_id": ...} must not be overwritten."""
        from query_web.request_context import reset_request_context, set_request_context

        tokens = set_request_context(
            correlation_id="from-contextvar", traceparent="", tracestate=""
        )
        try:
            log, buf = self._make_filtered_logger()
            log.info("explicit extra", extra={"correlation_id": "from-caller"})
            r = _record(buf)
            assert r["correlation_id"] == "from-caller"
        finally:
            reset_request_context(tokens)


# ---------------------------------------------------------------------------
# Redaction — sensitive keys
# ---------------------------------------------------------------------------


class TestRedactDict:
    @pytest.mark.parametrize(
        "key",
        [
            "authorization",
            "Authorization",
            "AUTHORIZATION",
            "api_key",
            "token",
            "secret",
            "password",
            "cookie",
            "set-cookie",
            "x-api-key",
            "client_secret",
            "access_token",
            "refresh_token",
            "sas_token",
            "connection_string",
        ],
    )
    def test_sensitive_key_value_is_redacted(self, key: str) -> None:
        result = _redact_dict({key: "super-secret-value"})
        assert result[key] == "***REDACTED***"

    def test_safe_key_value_is_preserved(self) -> None:
        result = _redact_dict({"operation": "read", "container": "state"})
        assert result["operation"] == "read"
        assert result["container"] == "state"

    def test_nested_sensitive_key_is_redacted(self) -> None:
        result = _redact_value({"headers": {"authorization": "Bearer tok123456789"}})
        assert result["headers"]["authorization"] == "***REDACTED***"

    def test_list_values_are_recursed(self) -> None:
        result = _redact_value([{"password": "abc"}, {"safe": "value"}])
        assert result[0]["password"] == "***REDACTED***"
        assert result[1]["safe"] == "value"


# ---------------------------------------------------------------------------
# Redaction — token patterns in message strings
# ---------------------------------------------------------------------------


class TestRedactStr:
    def test_bearer_token_in_message_is_redacted(self) -> None:
        msg = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        result = _redact_str(msg)
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "Bearer ***REDACTED***" in result

    def test_basic_auth_in_message_is_redacted(self) -> None:
        msg = "Sending Basic dXNlcjpwYXNzd29yZA=="
        result = _redact_str(msg)
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert "Basic ***REDACTED***" in result

    def test_short_token_not_redacted(self) -> None:
        # Bearer tokens < 10 chars are not redacted (avoid over-matching log IDs)
        msg = "Bearer short"
        result = _redact_str(msg)
        assert result == msg

    def test_safe_message_unchanged(self) -> None:
        msg = "Processing request for correlation_id=abc-123"
        assert _redact_str(msg) == msg


# ---------------------------------------------------------------------------
# Redaction — sensitive value appears in formatter output
# ---------------------------------------------------------------------------


class TestJsonFormatterRedaction:
    def test_sensitive_extra_key_redacted_in_output(self) -> None:
        log, buf = _capture_logger()
        log.info(
            "outbound call", extra={"authorization": "Bearer tok123456789abc", "route": "/api/ask"}
        )
        r = _record(buf)
        assert r["authorization"] == "***REDACTED***"
        assert r["route"] == "/api/ask"

    def test_bearer_in_message_redacted_in_output(self) -> None:
        log, buf = _capture_logger()
        log.info("Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
        r = _record(buf)
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9" not in r["message"]


# ---------------------------------------------------------------------------
# configure_logging — idempotent handler registration
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_configure_logging_idempotent(self) -> None:
        """Calling configure_logging twice must not double-register handlers."""
        configure_logging("svc-a")
        configure_logging("svc-b")
        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stream_handlers) == 1

    def test_configure_logging_sets_level(self) -> None:
        configure_logging("svc", level=logging.WARNING)
        assert logging.getLogger().level == logging.WARNING
        # Reset to avoid polluting other tests
        configure_logging("test", level=logging.DEBUG)


# ---------------------------------------------------------------------------
# query_web.log_config is a shim — verifies single source of truth
# ---------------------------------------------------------------------------


class TestQueryWebLogConfigIsShim:
    def test_formatter_class_is_identical(self) -> None:
        assert QJsonFormatter is JsonFormatter

    def test_filter_class_is_identical(self) -> None:
        assert QCorrelationContextFilter is CorrelationContextFilter

    def test_redact_dict_is_identical(self) -> None:
        assert _q_redact_dict is _redact_dict

    def test_redact_str_is_identical(self) -> None:
        assert _q_redact_str is _redact_str

    def test_extra_field_allowlist_is_identical(self) -> None:
        from runtime.log_config import _EXTRA_FIELD_ALLOWLIST

        assert _q_extra_field_allowlist is _EXTRA_FIELD_ALLOWLIST

    def test_max_field_value_len_is_identical(self) -> None:
        from runtime.log_config import _MAX_FIELD_VALUE_LEN

        assert _q_max_field_value_len is _MAX_FIELD_VALUE_LEN


# ---------------------------------------------------------------------------
# Runtime log_config — uses _runtime_context_getter (sanitizes via trace_context)
# ---------------------------------------------------------------------------


class TestRuntimeLogConfig:
    """Tests runtime.log_config with default (runtime) context getter."""

    def _make_runtime_filtered_logger(self) -> tuple[logging.Logger, StringIO]:
        from runtime.log_config import _runtime_context_getter

        buf = StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonFormatter("runtime-test"))
        handler.addFilter(CorrelationContextFilter(_runtime_context_getter))
        log = logging.getLogger(f"test.runtime.{id(buf)}")
        log.handlers = []
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        return log, buf

    def test_runtime_correlation_id_injected(self) -> None:
        from runtime.trace_context import reset_trace_context, set_trace_context

        tokens = set_trace_context(correlation_id="runtime-corr-001", traceparent="", tracestate="")
        try:
            log, buf = self._make_runtime_filtered_logger()
            log.info("runtime log line")
            r = _record(buf)
            assert r["correlation_id"] == "runtime-corr-001"
            assert r["service"] == "runtime-test"
        finally:
            reset_trace_context(tokens)

    def test_runtime_traceparent_injected(self) -> None:
        from runtime.trace_context import reset_trace_context, set_trace_context

        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        tokens = set_trace_context(correlation_id="c4", traceparent=tp, tracestate="")
        try:
            log, buf = self._make_runtime_filtered_logger()
            log.info("runtime trace")
            r = _record(buf)
            assert r["traceparent"] == tp
        finally:
            reset_trace_context(tokens)

    def test_runtime_no_context_omits_fields(self) -> None:
        from runtime.trace_context import _CORRELATION_ID, _TRACEPARENT, _TRACESTATE

        _CORRELATION_ID.set("")
        _TRACEPARENT.set("")
        _TRACESTATE.set("")

        log, buf = self._make_runtime_filtered_logger()
        log.info("no context")
        r = _record(buf)

        assert "correlation_id" not in r
        assert "traceparent" not in r

    def test_runtime_getter_sanitizes_unsafe_correlation_id(self) -> None:
        """_runtime_context_getter must apply _sanitize_correlation_id."""
        from runtime.trace_context import reset_trace_context, set_trace_context

        # Contains a newline — log-injection character; must be stripped
        tokens = set_trace_context(correlation_id="bad\nid", traceparent="", tracestate="")
        try:
            log, buf = self._make_runtime_filtered_logger()
            log.info("sanitize check")
            r = _record(buf)
            assert "\n" not in r.get("correlation_id", "")
        finally:
            reset_trace_context(tokens)

    def test_runtime_configure_logging_uses_runtime_getter_by_default(self) -> None:
        """configure_logging() with no context_getter must not raise."""
        _runtime_configure_logging("runtime-default-test")


# ---------------------------------------------------------------------------
# Extra-field allowlist enforcement
# ---------------------------------------------------------------------------


class TestExtraFieldAllowlist:
    def test_allowlisted_fields_are_emitted(self) -> None:
        log, buf = _capture_logger()
        log.info("event", extra={"event": "ask_failed", "exc_type": "ValueError", "corpus": "b"})
        r = _record(buf)
        assert r["event"] == "ask_failed"
        assert r["exc_type"] == "ValueError"
        assert r["corpus"] == "b"

    def test_unknown_fields_are_dropped(self) -> None:
        """Fields not in _EXTRA_FIELD_ALLOWLIST must not appear in emitted JSON."""
        log, buf = _capture_logger()
        log.info("msg", extra={"color_message": "red", "markup": True, "rich": True})
        r = _record(buf)
        assert "color_message" not in r
        assert "markup" not in r
        assert "rich" not in r
        # Stable schema fields must still be present
        assert "message" in r
        assert "timestamp" in r

    def test_private_prefixed_fields_are_dropped(self) -> None:
        log, buf = _capture_logger()
        log.info("msg", extra={"_private": "value", "event": "test_event"})
        r = _record(buf)
        assert "_private" not in r
        assert r["event"] == "test_event"

    def test_outbound_fields_are_emitted(self) -> None:
        log, buf = _capture_logger()
        log.info(
            "msg",
            extra={
                "event": "outbound_http_call",
                "provider": "http",
                "system": "azure-openai",
                "status": "ok",
                "retry_count": 2,
                "status_code": 200,
                "duration_ms": 12.5,
            },
        )
        r = _record(buf)
        assert r["provider"] == "http"
        assert r["system"] == "azure-openai"
        assert "target_service" not in r
        assert r["status"] == "ok"
        assert r["retry_count"] == 2
        assert r["status_code"] == 200
        assert r["duration_ms"] == 12.5


# ---------------------------------------------------------------------------
# Field value truncation
# ---------------------------------------------------------------------------


class TestFieldTruncation:
    def test_long_string_value_is_truncated(self) -> None:
        from runtime.log_config import _MAX_FIELD_VALUE_LEN

        long_val = "x" * (_MAX_FIELD_VALUE_LEN + 100)
        log, buf = _capture_logger()
        log.info("msg", extra={"event": "test", "operation": long_val})
        r = _record(buf)
        assert r["operation"] == "x" * _MAX_FIELD_VALUE_LEN + "…[truncated]"

    def test_exact_length_value_not_truncated(self) -> None:
        from runtime.log_config import _MAX_FIELD_VALUE_LEN

        exact_val = "x" * _MAX_FIELD_VALUE_LEN
        log, buf = _capture_logger()
        log.info("msg", extra={"event": "test", "operation": exact_val})
        r = _record(buf)
        assert r["operation"] == exact_val
        assert "truncated" not in r["operation"]

    def test_short_value_unchanged(self) -> None:
        log, buf = _capture_logger()
        log.info("msg", extra={"event": "test", "operation": "short"})
        r = _record(buf)
        assert r["operation"] == "short"

    def test_non_string_value_not_truncated(self) -> None:
        log, buf = _capture_logger()
        log.info("msg", extra={"event": "test", "count": 99999})
        r = _record(buf)
        assert r["count"] == 99999


# ---------------------------------------------------------------------------
# JWT pattern redaction
# ---------------------------------------------------------------------------


class TestJwtRedaction:
    _JWT = (
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )

    def test_bare_jwt_in_message_is_redacted(self) -> None:
        log, buf = _capture_logger()
        log.info(f"Token value: {self._JWT}")
        r = _record(buf)
        assert self._JWT not in r["message"]
        assert "***REDACTED***" in r["message"]

    def test_bearer_plus_jwt_in_message_is_redacted(self) -> None:
        """Bearer prefix already caught; redundant JWT match causes no double-redaction issues."""
        msg = f"Authorization: Bearer {self._JWT}"
        log, buf = _capture_logger()
        log.info(msg)
        r = _record(buf)
        assert self._JWT not in r["message"]
        assert "***REDACTED***" in r["message"]

    def test_short_ey_prefix_not_redacted(self) -> None:
        """Strings starting with 'ey' that are not JWTs (too short / no dots) are left alone."""
        log, buf = _capture_logger()
        log.info("eyOK")
        r = _record(buf)
        assert r["message"] == "eyOK"


# ---------------------------------------------------------------------------
# Stack trace redaction
# ---------------------------------------------------------------------------


class TestStackTraceRedaction:
    def test_bearer_in_exception_message_is_redacted_in_stack(self) -> None:
        token = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyIn0.signaturegoeshere_longvalue"
        )
        try:
            raise RuntimeError(f"HTTP 401 Bearer {token}")
        except RuntimeError:
            log, buf = _capture_logger()
            log.exception("auth failure")
        r = _record(buf)
        assert token not in r.get("stack", "")
        assert "***REDACTED***" in r.get("stack", "")

    def test_bare_jwt_in_exception_is_redacted_in_stack(self) -> None:
        jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        try:
            raise ValueError(f"Unexpected token: {jwt}")
        except ValueError:
            log, buf = _capture_logger()
            log.exception("parse error")
        r = _record(buf)
        assert jwt not in r.get("stack", "")
        assert "***REDACTED***" in r.get("stack", "")
