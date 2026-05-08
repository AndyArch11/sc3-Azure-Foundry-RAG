from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from query_web.request_context import (
    _sanitise_correlation_id,
    _sanitise_tracestate,
    _validate_traceparent,
    get_correlation_id,
    get_traceparent,
    get_tracestate,
    outbound_trace_headers,
    register_request_context_middleware,
)

_VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_VALID_TS = "vendor1=opaque1,vendor2=opaque2"


def _make_client() -> TestClient:
    app = FastAPI()
    register_request_context_middleware(app)

    @app.get("/ctx")
    def _ctx() -> dict[str, object]:
        outbound = outbound_trace_headers()
        return {
            "correlation_id": get_correlation_id(),
            "traceparent": get_traceparent(),
            "tracestate": get_tracestate(),
            "outbound": outbound,
        }

    return TestClient(app)


# ---------------------------------------------------------------------------
# Middleware basics
# ---------------------------------------------------------------------------


def test_middleware_generates_correlation_id_when_missing() -> None:
    client = _make_client()

    response = client.get("/ctx")

    assert response.status_code == 200
    generated = response.headers.get("x-correlation-id", "")
    assert generated
    assert response.json()["correlation_id"] == generated


def test_middleware_preserves_incoming_correlation_id() -> None:
    client = _make_client()

    response = client.get("/ctx", headers={"x-correlation-id": "corr-123"})

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "corr-123"
    assert response.json()["correlation_id"] == "corr-123"


def test_middleware_preserves_incoming_traceparent() -> None:
    client = _make_client()

    response = client.get("/ctx", headers={"traceparent": _VALID_TP})

    body = response.json()
    assert response.status_code == 200
    assert response.headers["traceparent"] == _VALID_TP
    assert body["traceparent"] == _VALID_TP
    assert body["outbound"]["traceparent"] == _VALID_TP


# ---------------------------------------------------------------------------
# tracestate propagation
# ---------------------------------------------------------------------------


def test_middleware_preserves_tracestate_with_valid_traceparent() -> None:
    client = _make_client()

    response = client.get(
        "/ctx",
        headers={"traceparent": _VALID_TP, "tracestate": _VALID_TS},
    )

    body = response.json()
    assert response.status_code == 200
    assert response.headers["tracestate"] == _VALID_TS
    assert body["tracestate"] == _VALID_TS
    assert body["outbound"]["tracestate"] == _VALID_TS


def test_middleware_drops_tracestate_when_traceparent_absent() -> None:
    """tracestate without a valid traceparent is meaningless and must not be forwarded."""
    client = _make_client()

    response = client.get("/ctx", headers={"tracestate": _VALID_TS})

    body = response.json()
    assert "tracestate" not in response.headers
    assert body["tracestate"] == ""
    assert "tracestate" not in body["outbound"]


def test_middleware_drops_tracestate_when_traceparent_invalid() -> None:
    client = _make_client()

    response = client.get(
        "/ctx",
        headers={"traceparent": "bad-value", "tracestate": _VALID_TS},
    )

    assert "tracestate" not in response.headers


def test_outbound_headers_include_tracestate_alongside_traceparent() -> None:
    """outbound_trace_headers must include tracestate iff traceparent is present."""
    client = _make_client()
    response = client.get("/ctx", headers={"traceparent": _VALID_TP, "tracestate": _VALID_TS})
    body = response.json()
    assert body["outbound"]["traceparent"] == _VALID_TP
    assert body["outbound"]["tracestate"] == _VALID_TS


# ---------------------------------------------------------------------------
# _validate_traceparent — per W3C spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        # Valid v00
        (
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        # Sampled flag 00 (unsampled) still valid — sampling is vendor responsibility
        (
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        # All-zero trace-id — invalid
        (
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            "",
        ),
        # All-zero parent-id — invalid
        (
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
            "",
        ),
        # Version ff — reserved, always invalid
        (
            "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "",
        ),
        # Malformed — too short
        ("00-short", ""),
        # Empty
        ("", ""),
        # Whitespace only
        ("   ", ""),
        # Invalid hex in trace-id
        (
            "00-4bf92f3577b34da6a3ce929d0e0e4GGG-00f067aa0ba902b7-01",
            "",
        ),
        # Unknown future version — salvage as v00
        (
            "02-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        # Unknown future version with trailing additional fields (spec allows this)
        (
            "02-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
    ],
)
def test_validate_traceparent(value: str, expected: str) -> None:
    assert _validate_traceparent(value) == expected


def test_middleware_drops_invalid_traceparent() -> None:
    """An invalid traceparent must be dropped rather than forwarded."""
    client = _make_client()

    response = client.get(
        "/ctx",
        headers={"traceparent": "00-00000000000000000000000000000000-00f067aa0ba902b7-01"},
    )

    assert "traceparent" not in response.headers
    assert response.json()["traceparent"] == ""


def test_middleware_rewrites_future_version_traceparent_as_v00() -> None:
    client = _make_client()
    future_tp = "02-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    expected_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    response = client.get("/ctx", headers={"traceparent": future_tp})

    assert response.headers["traceparent"] == expected_tp
    assert response.json()["traceparent"] == expected_tp


# ---------------------------------------------------------------------------
# _sanitise_correlation_id — log injection / DoS guards
# ---------------------------------------------------------------------------


def test_sanitise_correlation_id_strips_unsafe_chars() -> None:
    assert _sanitise_correlation_id("corr\r\n123") == "corr123"
    assert _sanitise_correlation_id("abc\x1b[31mred") == "abc31mred"
    assert _sanitise_correlation_id("ok-value_1.2") == "ok-value_1.2"


def test_sanitise_correlation_id_truncates_to_max_length() -> None:
    long_id = "a" * 200
    result = _sanitise_correlation_id(long_id)
    assert len(result) == 128


def test_middleware_sanitises_injected_correlation_id() -> None:
    """CRLF header injection is neutralised; safe chars are preserved."""
    client = _make_client()

    response = client.get("/ctx", headers={"x-correlation-id": "legit\r\nX-Injected: evil"})

    corr = response.headers["x-correlation-id"]
    # CRLF stripped — no new-line header injection possible
    assert "\r" not in corr
    assert "\n" not in corr
    # Colon and space stripped — the injected header name is harmless without them
    assert ":" not in corr
    assert corr.startswith("legit")


# ---------------------------------------------------------------------------
# _sanitise_tracestate — length cap
# ---------------------------------------------------------------------------


def test_sanitise_tracestate_caps_at_512_chars() -> None:
    long_ts = "v=x," * 200  # 800 chars
    result = _sanitise_tracestate(long_ts)
    assert len(result) <= 512


def test_middleware_caps_overlong_tracestate() -> None:
    client = _make_client()
    long_ts = "k=v," * 200

    response = client.get(
        "/ctx",
        headers={"traceparent": _VALID_TP, "tracestate": long_ts},
    )

    ts_in_response = response.headers.get("tracestate", "")
    assert len(ts_in_response) <= 512
