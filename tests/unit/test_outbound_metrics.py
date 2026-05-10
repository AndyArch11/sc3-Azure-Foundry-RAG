"""Unit tests for outbound metrics and instrumentation emission."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram


def _make_registry() -> CollectorRegistry:
    return CollectorRegistry()


def _make_metrics(registry: CollectorRegistry) -> dict[str, Counter | Histogram]:
    buckets = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    return {
        "http_requests": Counter(
            "outbound_http_requests_total",
            "Total outbound HTTP requests",
            labelnames=["provider", "system", "method", "status", "status_code", "operation"],
            registry=registry,
        ),
        "http_duration": Histogram(
            "outbound_http_duration_seconds",
            "Duration of outbound HTTP requests",
            labelnames=["provider", "system", "operation", "status"],
            buckets=buckets,
            registry=registry,
        ),
        "sdk_calls": Counter(
            "outbound_sdk_calls_total",
            "Total outbound SDK calls",
            labelnames=["provider", "system", "operation", "status"],
            registry=registry,
        ),
        "sdk_duration": Histogram(
            "outbound_sdk_duration_seconds",
            "Duration of outbound SDK calls",
            labelnames=["provider", "system", "operation", "status"],
            buckets=buckets,
            registry=registry,
        ),
    }


class TestOutboundMetricsModule:
    def test_metrics_available_flag_is_true(self) -> None:
        from runtime.outbound_metrics import metrics_available

        assert metrics_available is True

    def test_http_requests_counter_has_expected_labels(self) -> None:
        from runtime.outbound_metrics import OUTBOUND_HTTP_REQUESTS_TOTAL

        OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system="svc",
            method="GET",
            status="ok",
            status_code="200",
            operation="search",
        )

    def test_http_duration_histogram_has_expected_labels(self) -> None:
        from runtime.outbound_metrics import OUTBOUND_HTTP_DURATION_SECONDS

        OUTBOUND_HTTP_DURATION_SECONDS.labels(
            provider="http",
            system="svc",
            operation="search",
            status="ok",
        )

    def test_sdk_metrics_have_expected_labels(self) -> None:
        from runtime.outbound_metrics import OUTBOUND_SDK_CALLS_TOTAL, OUTBOUND_SDK_DURATION_SECONDS

        OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-search",
            operation="search",
            status="ok",
        )
        OUTBOUND_SDK_DURATION_SECONDS.labels(
            provider="sdk",
            system="azure-search",
            operation="search",
            status="ok",
        )


class TestOutboundMetricsCounters:
    def test_http_counter_increments(self) -> None:
        reg = _make_registry()
        m = _make_metrics(reg)

        m["http_requests"].labels(
            provider="http",
            system="mysvc",
            method="POST",
            status="ok",
            status_code="200",
            operation="embed",
        ).inc()

        value = reg.get_sample_value(
            "outbound_http_requests_total",
            {
                "provider": "http",
                "system": "mysvc",
                "method": "POST",
                "status": "ok",
                "status_code": "200",
                "operation": "embed",
            },
        )
        assert value == 1.0

    def test_http_duration_histogram_count_increments(self) -> None:
        reg = _make_registry()
        m = _make_metrics(reg)

        m["http_duration"].labels(
            provider="http",
            system="svc",
            operation="embed",
            status="ok",
        ).observe(0.05)

        count = reg.get_sample_value(
            "outbound_http_duration_seconds_count",
            {"provider": "http", "system": "svc", "operation": "embed", "status": "ok"},
        )
        assert count == 1.0

    def test_sdk_calls_counter_by_status(self) -> None:
        reg = _make_registry()
        m = _make_metrics(reg)

        m["sdk_calls"].labels(
            provider="sdk",
            system="azure-blob",
            operation="put_object",
            status="ok",
        ).inc()
        m["sdk_calls"].labels(
            provider="sdk",
            system="azure-blob",
            operation="put_object",
            status="error",
        ).inc()

        assert (
            reg.get_sample_value(
                "outbound_sdk_calls_total",
                {
                    "provider": "sdk",
                    "system": "azure-blob",
                    "operation": "put_object",
                    "status": "ok",
                },
            )
            == 1.0
        )
        assert (
            reg.get_sample_value(
                "outbound_sdk_calls_total",
                {
                    "provider": "sdk",
                    "system": "azure-blob",
                    "operation": "put_object",
                    "status": "error",
                },
            )
            == 1.0
        )


class TestRequestWithInstrumentationEmitsMetrics:
    def _fake_response(self, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        return resp

    def test_successful_request_increments_counter(self) -> None:
        import runtime.outbound_metrics as om
        from runtime.outbound_instrumentation import request_with_instrumentation

        logger = logging.getLogger("test.outbound.http.ok")
        fake_resp = self._fake_response(200)

        before = om.OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system="test-svc",
            method="GET",
            status="ok",
            status_code="200",
            operation="ping",
        )._value.get()

        request_with_instrumentation(
            "GET",
            "https://example.com/api",
            logger=logger,
            system="test-svc",
            operation="ping",
            request_callable=lambda url, **kw: fake_resp,
        )

        after = om.OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system="test-svc",
            method="GET",
            status="ok",
            status_code="200",
            operation="ping",
        )._value.get()
        assert after == before + 1.0

    def test_failed_request_increments_error_counter(self) -> None:
        import requests as _requests

        import runtime.outbound_metrics as om
        from runtime.outbound_instrumentation import request_with_instrumentation

        logger = logging.getLogger("test.outbound.http.err")

        def _fail(url: str, **kw: object) -> None:
            raise _requests.ConnectionError("simulated failure")

        before = om.OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system="failing-svc",
            method="GET",
            status="error",
            status_code="0",
            operation="check",
        )._value.get()

        with pytest.raises(_requests.ConnectionError):
            request_with_instrumentation(
                "GET",
                "https://failing.example.com/check",
                logger=logger,
                system="failing-svc",
                operation="check",
                request_callable=_fail,
            )

        after = om.OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system="failing-svc",
            method="GET",
            status="error",
            status_code="0",
            operation="check",
        )._value.get()
        assert after == before + 1.0

    def test_successful_request_observes_duration(self) -> None:
        from runtime.outbound_instrumentation import request_with_instrumentation

        logger = logging.getLogger("test.outbound.http.dur")
        fake_resp = self._fake_response(200)

        before_count = (
            REGISTRY.get_sample_value(
                "outbound_http_duration_seconds_count",
                {
                    "provider": "http",
                    "system": "dur-svc",
                    "operation": "fetch",
                    "status": "ok",
                },
            )
            or 0.0
        )

        request_with_instrumentation(
            "POST",
            "https://dur.example.com/fetch",
            logger=logger,
            system="dur-svc",
            operation="fetch",
            request_callable=lambda url, **kw: fake_resp,
        )

        after_count = (
            REGISTRY.get_sample_value(
                "outbound_http_duration_seconds_count",
                {
                    "provider": "http",
                    "system": "dur-svc",
                    "operation": "fetch",
                    "status": "ok",
                },
            )
            or 0.0
        )
        assert after_count == before_count + 1.0


class TestSdkCallWithInstrumentationEmitsMetrics:
    def test_successful_sdk_call_increments_ok_counter(self) -> None:
        import runtime.outbound_metrics as om
        from runtime.outbound_instrumentation import sdk_call_with_instrumentation

        logger = logging.getLogger("test.outbound.sdk.ok")

        before = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-search",
            operation="index_documents",
            status="ok",
        )._value.get()

        sdk_call_with_instrumentation(
            logger=logger,
            system="azure-search",
            operation="index_documents",
            call=lambda: {"result": "indexed"},
        )

        after = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-search",
            operation="index_documents",
            status="ok",
        )._value.get()
        assert after == before + 1.0

    def test_failed_sdk_call_increments_error_counter(self) -> None:
        import runtime.outbound_metrics as om
        from runtime.outbound_instrumentation import sdk_call_with_instrumentation

        logger = logging.getLogger("test.outbound.sdk.err")

        def _boom() -> None:
            raise RuntimeError("SDK boom")

        before = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-search",
            operation="delete_documents",
            status="error",
        )._value.get()

        with pytest.raises(RuntimeError, match="SDK boom"):
            sdk_call_with_instrumentation(
                logger=logger,
                system="azure-search",
                operation="delete_documents",
                call=_boom,
            )

        after = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-search",
            operation="delete_documents",
            status="error",
        )._value.get()
        assert after == before + 1.0

    def test_sdk_call_observes_duration(self) -> None:
        from runtime.outbound_instrumentation import sdk_call_with_instrumentation

        logger = logging.getLogger("test.outbound.sdk.dur")

        before_count = (
            REGISTRY.get_sample_value(
                "outbound_sdk_duration_seconds_count",
                {
                    "provider": "sdk",
                    "system": "azure-blob",
                    "operation": "get_object_metadata",
                    "status": "ok",
                },
            )
            or 0.0
        )

        sdk_call_with_instrumentation(
            logger=logger,
            system="azure-blob",
            operation="get_object_metadata",
            call=lambda: {"size": 1024},
        )

        after_count = (
            REGISTRY.get_sample_value(
                "outbound_sdk_duration_seconds_count",
                {
                    "provider": "sdk",
                    "system": "azure-blob",
                    "operation": "get_object_metadata",
                    "status": "ok",
                },
            )
            or 0.0
        )
        assert after_count == before_count + 1.0

    def test_expected_exception_records_expected_miss_not_error(self) -> None:
        import runtime.outbound_metrics as om
        from runtime.outbound_instrumentation import sdk_call_with_instrumentation

        logger = logging.getLogger("test.outbound.sdk.expected")

        before_expected = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-cosmos",
            operation="read_item",
            status="expected_miss",
        )._value.get()
        before_error = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-cosmos",
            operation="read_item",
            status="error",
        )._value.get()

        def _not_found() -> None:
            raise KeyError("not found")

        with pytest.raises(KeyError, match="not found"):
            sdk_call_with_instrumentation(
                logger=logger,
                system="azure-cosmos",
                operation="read_item",
                call=_not_found,
                expected_exceptions=(KeyError,),
            )

        after_expected = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-cosmos",
            operation="read_item",
            status="expected_miss",
        )._value.get()
        after_error = om.OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system="azure-cosmos",
            operation="read_item",
            status="error",
        )._value.get()

        assert after_expected == before_expected + 1.0
        assert after_error == before_error
