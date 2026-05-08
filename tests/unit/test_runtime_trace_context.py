from __future__ import annotations

import pytest

from runtime.trace_context import (
    _sanitise_correlation_id,
    _sanitise_tracestate,
    _validate_traceparent,
    outbound_trace_headers,
    scoped_trace_context,
)

_VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_VALID_TS = "vendor1=opaque1,vendor2=opaque2"


# ---------------------------------------------------------------------------
# outbound_trace_headers basic behaviour
# ---------------------------------------------------------------------------


def test_outbound_trace_headers_empty_without_context() -> None:
    assert outbound_trace_headers() == {}


def test_outbound_trace_headers_reads_scoped_context() -> None:
    with scoped_trace_context(correlation_id="corr-runtime-1", traceparent=_VALID_TP):
        headers = outbound_trace_headers()

    assert headers["x-correlation-id"] == "corr-runtime-1"
    assert headers["traceparent"] == _VALID_TP


# ---------------------------------------------------------------------------
# tracestate propagation
# ---------------------------------------------------------------------------


def test_outbound_trace_headers_includes_tracestate_with_traceparent() -> None:
    with scoped_trace_context(
        correlation_id="corr-ts-1",
        traceparent=_VALID_TP,
        tracestate=_VALID_TS,
    ):
        headers = outbound_trace_headers()

    assert headers["traceparent"] == _VALID_TP
    assert headers["tracestate"] == _VALID_TS


def test_outbound_trace_headers_omits_tracestate_without_traceparent() -> None:
    with scoped_trace_context(correlation_id="corr-ts-2", tracestate=_VALID_TS):
        headers = outbound_trace_headers()

    assert "traceparent" not in headers
    assert "tracestate" not in headers


# ---------------------------------------------------------------------------
# _validate_traceparent — W3C spec compliance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (_VALID_TP, _VALID_TP),
        # Unsampled flag — propagated faithfully, sampling is vendor responsibility
        (
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        # All-zero trace-id — invalid
        ("00-00000000000000000000000000000000-00f067aa0ba902b7-01", ""),
        # All-zero parent-id — invalid
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01", ""),
        # Reserved version ff — always invalid
        ("ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", ""),
        # Unknown future version — salvage as v00
        (
            "02-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        # Completely malformed
        ("not-a-traceparent", ""),
        # Empty
        ("", ""),
    ],
)
def test_validate_traceparent_runtime(value: str, expected: str) -> None:
    assert _validate_traceparent(value) == expected


# ---------------------------------------------------------------------------
# _sanitise_correlation_id — log injection / DoS guards
# ---------------------------------------------------------------------------


def test_sanitise_correlation_id_strips_crlf_and_escape() -> None:
    assert _sanitise_correlation_id("corr\r\ninjected") == "corrinjected"
    assert _sanitise_correlation_id("id\x1b[31mred") == "id31mred"
    assert _sanitise_correlation_id("safe-id_1.2") == "safe-id_1.2"


def test_sanitise_correlation_id_caps_length() -> None:
    assert len(_sanitise_correlation_id("a" * 200)) == 128


# ---------------------------------------------------------------------------
# _sanitise_tracestate — length cap
# ---------------------------------------------------------------------------


def test_sanitise_tracestate_caps_at_512() -> None:
    result = _sanitise_tracestate("k=v," * 200)
    assert len(result) <= 512
