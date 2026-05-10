from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from runtime.outbound_metrics import (
        OUTBOUND_HTTP_DURATION_SECONDS,
        OUTBOUND_HTTP_REQUESTS_TOTAL,
        OUTBOUND_SDK_CALLS_TOTAL,
        OUTBOUND_SDK_DURATION_SECONDS,
    )
except ModuleNotFoundError:
    from outbound_metrics import OUTBOUND_HTTP_DURATION_SECONDS  # noqa: PLC0415  # type: ignore[no-redef]
    from outbound_metrics import OUTBOUND_HTTP_REQUESTS_TOTAL  # noqa: PLC0415  # type: ignore[no-redef]
    from outbound_metrics import OUTBOUND_SDK_CALLS_TOTAL  # noqa: PLC0415  # type: ignore[no-redef]
    from outbound_metrics import OUTBOUND_SDK_DURATION_SECONDS  # noqa: PLC0415  # type: ignore[no-redef]

try:
    from runtime.trace_context import outbound_trace_headers as _runtime_outbound_trace_headers
except ModuleNotFoundError:
    from trace_context import outbound_trace_headers as _runtime_outbound_trace_headers  # type: ignore[no-redef]

_DEFAULT_EVENT = "outbound_http_call"
_HTTP_ERROR_EVENT = "outbound_http_error"
_SDK_SUCCESS_EVENT = "outbound_sdk_call"
_SDK_ERROR_EVENT = "outbound_sdk_error"

# explicit mapping from log event names to Prometheus
# metrics emitted by this module.
_EVENT_TO_METRICS: dict[str, tuple[str, str]] = {
    _DEFAULT_EVENT: ("outbound_http_requests_total", "outbound_http_duration_seconds"),
    _HTTP_ERROR_EVENT: ("outbound_http_requests_total", "outbound_http_duration_seconds"),
    _SDK_SUCCESS_EVENT: ("outbound_sdk_calls_total", "outbound_sdk_duration_seconds"),
    _SDK_ERROR_EVENT: ("outbound_sdk_calls_total", "outbound_sdk_duration_seconds"),
}


def _merge_headers(
    headers: Mapping[str, str] | None,
    *,
    header_getter: Callable[[], dict[str, str]],
) -> dict[str, str]:
    merged = dict(headers or {})
    propagated = header_getter() or {}
    for key, value in propagated.items():
        merged.setdefault(key, value)
    return merged


def _target_and_endpoint(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    target = parsed.netloc or parsed.path or "unknown"
    endpoint = parsed.path or "/"
    return target, endpoint


def request_with_instrumentation(
    method: str,
    url: str,
    *,
    logger: logging.Logger,
    session: requests.Session | None = None,
    headers: Mapping[str, str] | None = None,
    retry_count: int = 0,
    system: str | None = None,
    operation: str | None = None,
    event: str = _DEFAULT_EVENT,
    header_getter: Callable[[], dict[str, str]] = _runtime_outbound_trace_headers,
    request_callable: Callable[..., requests.Response] | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Issue one outbound HTTP request with trace propagation and timing logs."""
    target, endpoint = _target_and_endpoint(url)
    merged_headers = _merge_headers(headers, header_getter=header_getter)

    started = time.perf_counter()
    try:
        if request_callable is not None:
            call_kwargs = dict(kwargs)
            if merged_headers:
                call_kwargs["headers"] = merged_headers
            response = request_callable(url, **call_kwargs)
        elif session is None:
            response = requests.request(method=method.upper(), url=url, headers=merged_headers, **kwargs)
        else:
            response = requests.Session.request(
                session,
                method=method.upper(),
                url=url,
                headers=merged_headers,
                **kwargs,
            )
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        _svc = system or target
        _op = operation or "http_request"
        _status = "ok"
        OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system=_svc,
            method=method.upper(),
            status=_status,
            status_code=str(response.status_code),
            operation=_op,
        ).inc()
        OUTBOUND_HTTP_DURATION_SECONDS.labels(
            provider="http",
            system=_svc,
            operation=_op,
            status=_status,
        ).observe(duration_ms / 1000.0)
        logger.info(
            "Outbound HTTP request completed",
            extra={
                "event": event,
                "direction": "outbound",
                "provider": "http",
                "target": target,
                "system": _svc,
                "endpoint": endpoint,
                "method": method.upper(),
                "status": _status,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "retry_count": retry_count,
                "operation": _op,
            },
        )
        return response
    except requests.RequestException as exc:
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        status_code = getattr(getattr(exc, "response", None), "status_code", 0)
        _svc = system or target
        _op = operation or "http_request"
        _status = "error"
        OUTBOUND_HTTP_REQUESTS_TOTAL.labels(
            provider="http",
            system=_svc,
            method=method.upper(),
            status=_status,
            status_code=str(int(status_code or 0)),
            operation=_op,
        ).inc()
        OUTBOUND_HTTP_DURATION_SECONDS.labels(
            provider="http",
            system=_svc,
            operation=_op,
            status=_status,
        ).observe(duration_ms / 1000.0)
        logger.warning(
            "Outbound HTTP request failed",
            exc_info=True,
            extra={
                "event": _HTTP_ERROR_EVENT,
                "direction": "outbound",
                "provider": "http",
                "target": target,
                "system": _svc,
                "endpoint": endpoint,
                "method": method.upper(),
                "status": _status,
                "status_code": int(status_code or 0),
                "duration_ms": duration_ms,
                "retry_count": retry_count,
                "operation": _op,
                "exc_type": type(exc).__name__,
            },
        )
        raise


class InstrumentedRequestsSession(requests.Session):
    """Requests Session with automatic trace propagation and outbound logs."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        system: str | None = None,
        header_getter: Callable[[], dict[str, str]] = _runtime_outbound_trace_headers,
    ) -> None:
        super().__init__()
        self._logger = logger
        self._system = system
        self._header_getter = header_getter

    def request(  # type: ignore[override]  # intentionally narrows bytes→str internally
        self, method: str | bytes, url: str | bytes, **kwargs: Any
    ) -> requests.Response:  # noqa: D401
        headers = kwargs.pop("headers", None)
        return request_with_instrumentation(
            method if isinstance(method, str) else method.decode(),
            url if isinstance(url, str) else url.decode(),
            logger=self._logger,
            session=self,
            headers=headers,
            system=self._system,
            header_getter=self._header_getter,
            **kwargs,
        )

    def post(  # type: ignore[override]  # forward extra kwargs (e.g. operation=) to request()
        self, url: str | bytes, data: Any = None, json: Any = None, **kwargs: Any
    ) -> requests.Response:
        return self.request("POST", url, data=data, json=json, **kwargs)


def sdk_call_with_instrumentation(
    *,
    logger: logging.Logger,
    system: str,
    operation: str,
    call: Callable[[], Any],
    expected_exceptions: tuple[type[BaseException], ...] = (),
) -> Any:
    """Run one SDK call and emit consistent operation-level telemetry."""
    started = time.perf_counter()
    try:
        result = call()
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        _status = "ok"
        OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system=system,
            operation=operation,
            status=_status,
        ).inc()
        OUTBOUND_SDK_DURATION_SECONDS.labels(
            provider="sdk",
            system=system,
            operation=operation,
            status=_status,
        ).observe(duration_ms / 1000.0)
        logger.info(
            "Outbound SDK call completed",
            extra={
                "event": _SDK_SUCCESS_EVENT,
                "direction": "outbound",
                "provider": "sdk",
                "target": system,
                "system": system,
                "operation": operation,
                "status": _status,
                "status_code": 200,
                "duration_ms": duration_ms,
                "retry_count": 0,
            },
        )
        return result
    except Exception as exc:
        if expected_exceptions and isinstance(exc, expected_exceptions):
            duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            _status = "expected_miss"
            OUTBOUND_SDK_CALLS_TOTAL.labels(
                provider="sdk",
                system=system,
                operation=operation,
                status=_status,
            ).inc()
            OUTBOUND_SDK_DURATION_SECONDS.labels(
                provider="sdk",
                system=system,
                operation=operation,
                status=_status,
            ).observe(duration_ms / 1000.0)
            logger.info(
                "Outbound SDK call expected miss",
                extra={
                    "event": _SDK_SUCCESS_EVENT,
                    "direction": "outbound",
                    "provider": "sdk",
                    "target": system,
                    "system": system,
                    "operation": operation,
                    "status": _status,
                    "status_code": 404,
                    "duration_ms": duration_ms,
                    "retry_count": 0,
                    "exc_type": type(exc).__name__,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        _status = "error"
        OUTBOUND_SDK_CALLS_TOTAL.labels(
            provider="sdk",
            system=system,
            operation=operation,
            status=_status,
        ).inc()
        OUTBOUND_SDK_DURATION_SECONDS.labels(
            provider="sdk",
            system=system,
            operation=operation,
            status=_status,
        ).observe(duration_ms / 1000.0)
        logger.warning(
            "Outbound SDK call failed",
            exc_info=True,
            extra={
                "event": _SDK_ERROR_EVENT,
                "direction": "outbound",
                "provider": "sdk",
                "target": system,
                "system": system,
                "operation": operation,
                "status": _status,
                "status_code": 0,
                "duration_ms": duration_ms,
                "retry_count": 0,
                "exc_type": type(exc).__name__,
            },
        )
        raise
