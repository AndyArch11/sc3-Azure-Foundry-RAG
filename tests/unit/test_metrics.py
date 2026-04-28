"""Unit tests for query_web/metrics.py.

Covers:
- observe_rag_metrics increments counters and observes histograms
- RAG_RETRY_TOTAL only increments when iterations >= 3
- observe_cosmos_schema_access increments correct counters
- COSMOS_SCHEMA_UPCASTS only increments when upcasted=True
- metrics_endpoint returns Prometheus text format with correct content-type
- register_metrics_endpoint adds a /metrics route to a FastAPI app
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _counter_value(counter: object, **labels: str) -> float:
    """Return the current value of a prometheus_client Counter (labelled or not)."""
    if labels:
        return float(counter.labels(**labels)._value.get())  # type: ignore[attr-defined]
    return float(counter._value.get())  # type: ignore[attr-defined]


def _histogram_count(histogram: object) -> float:
    """Return the total number of observations recorded on a Histogram (via _count sample).

    Sums across all label combinations so labelled histograms are handled correctly.
    """
    return sum(  # type: ignore[attr-defined]
        float(sample.value)
        for sample in histogram.collect()[0].samples  # type: ignore[attr-defined]
        if sample.name.endswith("_count")
    )


# ---------------------------------------------------------------------------
# observe_rag_metrics
# ---------------------------------------------------------------------------


class TestObserveRagMetrics:
    def test_increments_request_counter(self) -> None:
        from query_web.metrics import RAG_REQUESTS_TOTAL, observe_rag_metrics

        before = _counter_value(RAG_REQUESTS_TOTAL)
        observe_rag_metrics(
            {"total_s": 1.0, "rag_retrieval_s": 0.2, "llm_total_s": 0.7, "evaluator_s": 0.1}
        )
        assert _counter_value(RAG_REQUESTS_TOTAL) == before + 1

    def test_observes_total_s_histogram(self) -> None:
        from query_web.metrics import RAG_REQUEST_DURATION, observe_rag_metrics

        before = _histogram_count(RAG_REQUEST_DURATION)
        observe_rag_metrics({"total_s": 1.5})
        assert _histogram_count(RAG_REQUEST_DURATION) == before + 1

    def test_observes_retrieval_histogram(self) -> None:
        from query_web.metrics import RETRIEVAL_DURATION, observe_rag_metrics

        before = _histogram_count(RETRIEVAL_DURATION)
        observe_rag_metrics({"rag_retrieval_s": 0.3})
        assert _histogram_count(RETRIEVAL_DURATION) == before + 1

    def test_observes_llm_histogram(self) -> None:
        from query_web.metrics import LLM_DURATION, observe_rag_metrics

        before = _histogram_count(LLM_DURATION)
        observe_rag_metrics({"llm_total_s": 2.1})
        assert _histogram_count(LLM_DURATION) == before + 1

    def test_observes_evaluator_histogram(self) -> None:
        from query_web.metrics import EVALUATOR_DURATION, observe_rag_metrics

        before = _histogram_count(EVALUATOR_DURATION)
        observe_rag_metrics({"evaluator_s": 0.5})
        assert _histogram_count(EVALUATOR_DURATION) == before + 1

    def test_retry_counter_not_incremented_for_iterations_2(self) -> None:
        from query_web.metrics import RAG_RETRY_TOTAL, observe_rag_metrics

        before = _counter_value(RAG_RETRY_TOTAL)
        observe_rag_metrics({"total_s": 1.0}, iterations=2)
        assert _counter_value(RAG_RETRY_TOTAL) == before

    def test_retry_counter_incremented_for_iterations_3(self) -> None:
        from query_web.metrics import RAG_RETRY_TOTAL, observe_rag_metrics

        before = _counter_value(RAG_RETRY_TOTAL)
        observe_rag_metrics({"total_s": 1.0}, iterations=3)
        assert _counter_value(RAG_RETRY_TOTAL) == before + 1

    def test_missing_metric_keys_do_not_raise(self) -> None:
        from query_web.metrics import observe_rag_metrics

        observe_rag_metrics({})


# ---------------------------------------------------------------------------
# observe_cosmos_schema_access
# ---------------------------------------------------------------------------


class TestObserveCosmosSchemaAccess:
    def test_read_increments_reads_counter(self) -> None:
        from query_web.metrics import COSMOS_SCHEMA_VERSION_READS, observe_cosmos_schema_access

        before = _counter_value(
            COSMOS_SCHEMA_VERSION_READS,
            schema_version="v1",
            container="conversations",
            service="query-web",
        )
        observe_cosmos_schema_access(
            operation="read",
            container="conversations",
            schema_version_read="v1",
            schema_version_written="",
            upcasted=False,
            service="query-web",
        )
        assert (
            _counter_value(
                COSMOS_SCHEMA_VERSION_READS,
                schema_version="v1",
                container="conversations",
                service="query-web",
            )
            == before + 1
        )

    def test_upsert_increments_both_reads_and_writes(self) -> None:
        from query_web.metrics import (
            COSMOS_SCHEMA_VERSION_READS,
            COSMOS_SCHEMA_VERSION_WRITES,
            observe_cosmos_schema_access,
        )

        before_reads = _counter_value(
            COSMOS_SCHEMA_VERSION_READS, schema_version="v1", container="c1", service="svc"
        )
        before_writes = _counter_value(
            COSMOS_SCHEMA_VERSION_WRITES, schema_version="v1", container="c1", service="svc"
        )
        observe_cosmos_schema_access(
            operation="upsert",
            container="c1",
            schema_version_read="v1",
            schema_version_written="v1",
            upcasted=False,
            service="svc",
        )
        assert (
            _counter_value(
                COSMOS_SCHEMA_VERSION_READS, schema_version="v1", container="c1", service="svc"
            )
            == before_reads + 1
        )
        assert (
            _counter_value(
                COSMOS_SCHEMA_VERSION_WRITES, schema_version="v1", container="c1", service="svc"
            )
            == before_writes + 1
        )

    def test_upcast_counter_increments_when_upcasted_true(self) -> None:
        from query_web.metrics import COSMOS_SCHEMA_UPCASTS, observe_cosmos_schema_access

        before = _counter_value(COSMOS_SCHEMA_UPCASTS, container="c2", service="svc2")
        observe_cosmos_schema_access(
            operation="read",
            container="c2",
            schema_version_read="v0",
            schema_version_written="",
            upcasted=True,
            service="svc2",
        )
        assert _counter_value(COSMOS_SCHEMA_UPCASTS, container="c2", service="svc2") == before + 1

    def test_upcast_counter_not_incremented_when_upcasted_false(self) -> None:
        from query_web.metrics import COSMOS_SCHEMA_UPCASTS, observe_cosmos_schema_access

        before = _counter_value(COSMOS_SCHEMA_UPCASTS, container="c3", service="svc3")
        observe_cosmos_schema_access(
            operation="read",
            container="c3",
            schema_version_read="v1",
            schema_version_written="",
            upcasted=False,
            service="svc3",
        )
        assert _counter_value(COSMOS_SCHEMA_UPCASTS, container="c3", service="svc3") == before


# ---------------------------------------------------------------------------
# /metrics HTTP endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def _app(self) -> FastAPI:
        from query_web.metrics import register_metrics_endpoint

        app = FastAPI()
        register_metrics_endpoint(app)
        return app

    def test_returns_200(self) -> None:
        client = TestClient(self._app())
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_is_prometheus_text(self) -> None:
        client = TestClient(self._app())
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_body_contains_known_metric_name(self) -> None:
        client = TestClient(self._app())
        resp = client.get("/metrics")
        assert "rag_requests_total" in resp.text

    def test_not_in_openapi_schema(self) -> None:
        app = self._app()
        client = TestClient(app)
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/metrics" not in paths


# ---------------------------------------------------------------------------
# Trace validation counters
# ---------------------------------------------------------------------------


class TestTraceDroppedCounter:
    def test_malformed_increments_malformed_reason(self) -> None:
        from query_web.metrics import TRACE_DROPPED_TOTAL
        from query_web.request_context import _validate_traceparent

        before = _counter_value(TRACE_DROPPED_TOTAL, reason="malformed")
        _validate_traceparent("not-valid-at-all")
        assert _counter_value(TRACE_DROPPED_TOTAL, reason="malformed") == before + 1

    def test_all_zeros_increments_all_zeros_reason(self) -> None:
        from query_web.metrics import TRACE_DROPPED_TOTAL
        from query_web.request_context import _validate_traceparent

        before = _counter_value(TRACE_DROPPED_TOTAL, reason="all_zeros")
        _validate_traceparent("00-00000000000000000000000000000000-0000000000000000-00")
        assert _counter_value(TRACE_DROPPED_TOTAL, reason="all_zeros") == before + 1

    def test_reserved_version_ff_increments_reserved_version_reason(self) -> None:
        from query_web.metrics import TRACE_DROPPED_TOTAL
        from query_web.request_context import _validate_traceparent

        before = _counter_value(TRACE_DROPPED_TOTAL, reason="reserved_version")
        _validate_traceparent("ff-00112233445566778899aabbccddeeff-0011223344556677-01")
        assert _counter_value(TRACE_DROPPED_TOTAL, reason="reserved_version") == before + 1

    def test_valid_traceparent_does_not_increment(self) -> None:
        from query_web.metrics import TRACE_DROPPED_TOTAL
        from query_web.request_context import _validate_traceparent

        before_malformed = _counter_value(TRACE_DROPPED_TOTAL, reason="malformed")
        before_zeros = _counter_value(TRACE_DROPPED_TOTAL, reason="all_zeros")
        before_reserved = _counter_value(TRACE_DROPPED_TOTAL, reason="reserved_version")
        _validate_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        assert _counter_value(TRACE_DROPPED_TOTAL, reason="malformed") == before_malformed
        assert _counter_value(TRACE_DROPPED_TOTAL, reason="all_zeros") == before_zeros
        assert _counter_value(TRACE_DROPPED_TOTAL, reason="reserved_version") == before_reserved


class TestTraceSalvagedCounter:
    def test_future_version_increments_salvaged(self) -> None:
        from query_web.metrics import TRACE_SALVAGED_TOTAL
        from query_web.request_context import _validate_traceparent

        before = _counter_value(TRACE_SALVAGED_TOTAL)
        # Version 01 (future) — should be salvaged to v00
        result = _validate_traceparent("01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        assert result != ""
        assert result.startswith("00-")
        assert _counter_value(TRACE_SALVAGED_TOTAL) == before + 1

    def test_valid_v00_does_not_increment_salvaged(self) -> None:
        from query_web.metrics import TRACE_SALVAGED_TOTAL
        from query_web.request_context import _validate_traceparent

        before = _counter_value(TRACE_SALVAGED_TOTAL)
        _validate_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        assert _counter_value(TRACE_SALVAGED_TOTAL) == before


class TestCorrelationIdSanitizedCounter:
    def test_unsafe_chars_increment_counter(self) -> None:
        from query_web.metrics import CORRELATION_ID_SANITISED_TOTAL
        from query_web.request_context import _sanitise_correlation_id

        before = _counter_value(CORRELATION_ID_SANITISED_TOTAL)
        _sanitise_correlation_id("bad\r\nvalue")
        assert _counter_value(CORRELATION_ID_SANITISED_TOTAL) == before + 1

    def test_safe_value_does_not_increment_counter(self) -> None:
        from query_web.metrics import CORRELATION_ID_SANITISED_TOTAL
        from query_web.request_context import _sanitise_correlation_id

        before = _counter_value(CORRELATION_ID_SANITISED_TOTAL)
        _sanitise_correlation_id("safe-correlation-id.123")
        assert _counter_value(CORRELATION_ID_SANITISED_TOTAL) == before

    def test_oversized_value_without_unsafe_chars_does_not_increment(self) -> None:
        # Truncation alone does not count as sanitization (no chars removed, just sliced)
        from query_web.metrics import CORRELATION_ID_SANITISED_TOTAL
        from query_web.request_context import _sanitise_correlation_id

        before = _counter_value(CORRELATION_ID_SANITISED_TOTAL)
        _sanitise_correlation_id("a" * 200)
        assert _counter_value(CORRELATION_ID_SANITISED_TOTAL) == before


# ---------------------------------------------------------------------------
# HTTP request duration histogram
# ---------------------------------------------------------------------------


def _make_instrumented_client() -> TestClient:
    from query_web.request_context import register_request_context_middleware

    app = FastAPI()
    register_request_context_middleware(app)

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(app, raise_server_exceptions=False)


class TestHttpRequestDurationHistogram:
    def test_histogram_increments_after_request(self) -> None:
        from query_web.metrics import HTTP_REQUEST_DURATION

        client = _make_instrumented_client()

        before = _histogram_count(HTTP_REQUEST_DURATION)
        client.get("/ping")
        assert _histogram_count(HTTP_REQUEST_DURATION) == before + 1

    def test_histogram_records_method_path_status_labels(self) -> None:
        from query_web.metrics import HTTP_REQUEST_DURATION

        client = _make_instrumented_client()
        client.get("/ping")

        # Verify that at least one labelled sample exists with method=GET and status=200
        found = False
        for metric_family in HTTP_REQUEST_DURATION.collect():
            for sample in metric_family.samples:
                labels = sample.labels
                if (
                    labels.get("method") == "GET"
                    and labels.get("path") == "/ping"
                    and labels.get("status") == "200"
                    and sample.name.endswith("_count")
                    and sample.value >= 1
                ):
                    found = True
                    break
        assert found, "Expected labelled HTTP_REQUEST_DURATION sample for GET /ping 200"
