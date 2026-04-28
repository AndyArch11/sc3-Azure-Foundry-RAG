"""Integration-style unit tests: end-to-end telemetry correlation.

These tests exercise the full inbound→log→outbound chain using a minimal
FastAPI app with the real request-context middleware, the real log filter, and
the real outbound instrumentation — no network I/O required.

Covered scenarios
-----------------
* Log records emitted during a request handler carry the *inbound*
  ``x-correlation-id`` (middleware sets context → log filter injects field).
* ``request_with_instrumentation`` called inside a request handler forwards the
  inbound ``correlation_id`` on outbound HTTP headers (via query_web context getter).
* A request without ``x-correlation-id`` has a generated ID echoed on both
  the response header and the log records.
* Inbound ``traceparent`` is forwarded to outbound headers.
"""

from __future__ import annotations

import json
import logging
from io import StringIO
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from query_web.log_config import _query_web_context_getter
from query_web.request_context import (
    CORRELATION_ID_HEADER,
    TRACEPARENT_HEADER,
)
from query_web.request_context import outbound_trace_headers as _qw_outbound_trace_headers
from query_web.request_context import (
    register_request_context_middleware,
)
from runtime.log_config import CorrelationContextFilter, JsonFormatter

_VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_capturing_logger(service: str = "test-telemetry") -> tuple[logging.Logger, StringIO]:
    """Return a logger wired with the query-web JSON formatter + context filter."""
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter(service))
    handler.addFilter(CorrelationContextFilter(_query_web_context_getter))
    log = logging.getLogger(f"test.telemetry.integration.{id(buf)}")
    log.handlers = [handler]
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return log, buf


def _records(buf: StringIO) -> list[dict[str, Any]]:
    buf.seek(0)
    return [json.loads(ln) for ln in buf.read().splitlines() if ln.strip()]


def _make_app_with_logger(
    log: logging.Logger,
) -> tuple[FastAPI, TestClient]:
    """Build a minimal FastAPI app that emits one log line per request."""
    app = FastAPI()
    register_request_context_middleware(app)

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        log.info("inside probe handler")
        return JSONResponse({"ok": True})

    return app, TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. Log records contain inbound correlation_id
# ---------------------------------------------------------------------------


class TestLogRecordsCarryInboundCorrelation:
    def test_inbound_correlation_id_injected_into_log_records(self) -> None:
        """Every log record emitted during a request must carry the inbound correlation_id."""
        log, buf = _make_capturing_logger()
        _, client = _make_app_with_logger(log)

        resp = client.get("/probe", headers={CORRELATION_ID_HEADER: "inbound-corr-001"})

        assert resp.status_code == 200
        records = _records(buf)
        assert records, "No log records were captured during the request"
        for r in records:
            assert (
                r.get("correlation_id") == "inbound-corr-001"
            ), f"Record missing expected correlation_id: {r}"

    def test_inbound_traceparent_injected_into_log_records(self) -> None:
        """Log records must carry the validated inbound traceparent."""
        log, buf = _make_capturing_logger()
        _, client = _make_app_with_logger(log)

        resp = client.get(
            "/probe",
            headers={CORRELATION_ID_HEADER: "corr-tp-002", TRACEPARENT_HEADER: _VALID_TP},
        )

        assert resp.status_code == 200
        records = _records(buf)
        assert records
        for r in records:
            assert r.get("traceparent") == _VALID_TP, f"Record missing expected traceparent: {r}"

    def test_generated_correlation_id_appears_in_log_records(self) -> None:
        """When no x-correlation-id is sent, a generated ID is injected into logs."""
        log, buf = _make_capturing_logger()
        app = FastAPI()
        register_request_context_middleware(app)

        @app.get("/gen")
        async def gen(request: Request) -> JSONResponse:
            log.info("generated id handler")
            return JSONResponse({"ok": True})

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/gen")

        assert resp.status_code == 200
        echoed = resp.headers.get(CORRELATION_ID_HEADER, "")
        assert echoed, "Middleware must echo a generated correlation_id on the response"

        records = _records(buf)
        assert records
        for r in records:
            # Must match the echoed value (same generated ID used end-to-end)
            assert (
                r.get("correlation_id") == echoed
            ), f"Log record correlation_id {r.get('correlation_id')!r} != echoed {echoed!r}"


# ---------------------------------------------------------------------------
# 2. Outbound calls carry inbound correlation
# ---------------------------------------------------------------------------


class TestOutboundCallsCarryInboundCorrelation:
    def test_request_with_instrumentation_forwards_inbound_correlation(self) -> None:
        """request_with_instrumentation must put the inbound correlation_id on outbound headers."""
        from query_web.request_context import reset_request_context, set_request_context
        from runtime.outbound_instrumentation import request_with_instrumentation

        captured: list[dict[str, Any]] = []

        def _fake_call(url: str, **kwargs: Any) -> Any:
            captured.append(kwargs)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        tokens = set_request_context(
            correlation_id="inbound-corr-outbound",
            traceparent=_VALID_TP,
            tracestate="",
        )
        try:
            logger = logging.getLogger("test.telemetry.outbound")
            request_with_instrumentation(
                "GET",
                "https://example.downstream/api",
                logger=logger,
                request_callable=_fake_call,
                header_getter=_qw_outbound_trace_headers,
            )
        finally:
            reset_request_context(tokens)

        assert captured, "request_callable was never invoked"
        headers = captured[0].get("headers", {})
        assert headers.get(CORRELATION_ID_HEADER) == "inbound-corr-outbound"
        assert headers.get(TRACEPARENT_HEADER) == _VALID_TP

    def test_outbound_call_inside_request_handler_carries_correlation(self) -> None:
        """Full path: inbound request → middleware → outbound call inherits correlation."""
        from runtime.outbound_instrumentation import request_with_instrumentation

        captured: list[dict[str, Any]] = []

        def _fake_call(url: str, **kwargs: Any) -> Any:
            captured.append(kwargs)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        app = FastAPI()
        register_request_context_middleware(app)

        @app.get("/with-outbound")
        async def with_outbound(request: Request) -> JSONResponse:
            logger = logging.getLogger("test.telemetry.outbound.handler")
            request_with_instrumentation(
                "GET",
                "https://example.downstream/data",
                logger=logger,
                request_callable=_fake_call,
                header_getter=_qw_outbound_trace_headers,
            )
            return JSONResponse({"ok": True})

        client = TestClient(app, raise_server_exceptions=True)
        client.get(
            "/with-outbound",
            headers={
                CORRELATION_ID_HEADER: "full-path-corr-003",
                TRACEPARENT_HEADER: _VALID_TP,
            },
        )

        assert captured, "Outbound call was never made inside the handler"
        headers = captured[0].get("headers", {})
        assert headers.get(CORRELATION_ID_HEADER) == "full-path-corr-003"
        assert headers.get(TRACEPARENT_HEADER) == _VALID_TP
