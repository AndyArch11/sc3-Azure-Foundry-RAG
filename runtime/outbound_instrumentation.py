"""
    Outbound instrumentation for HTTP requests and SDK calls, including trace propagation and Prometheus metrics.
"""
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
    """Merge provided headers with propagated trace headers, giving precedence to provided headers.

    Args:
        headers: Optional dictionary of headers to include in the request.
        header_getter: Callable that returns a dictionary of headers to propagate (e.g., trace headers).
    Returns:
        A dictionary containing the merged headers, with provided headers taking precedence over propagated headers.
    """
    merged = dict(headers or {})
    propagated = header_getter() or {}
    for key, value in propagated.items():
        merged.setdefault(key, value)
    return merged


def _target_and_endpoint(url: str) -> tuple[str, str]:
    """Extract the target and endpoint from a URL.

    Args:
        url: The URL to parse.

    Returns:
        A tuple containing the target (host) and endpoint (path) of the URL.
    """
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
    """Issue one outbound HTTP request with trace propagation and timing logs.

    Args:
        method: The HTTP method to use for the request.
        url: The URL to send the request to.
        logger: The logger to use for logging request details.
        session: Optional requests.Session to use for the request.
        headers: Optional dictionary of headers to include in the request.
        retry_count: The number of times the request has been retried.
        system: Optional system name for metrics and logging.
        operation: Optional operation name for metrics and logging.
        event: The event name for logging.
        header_getter: Callable that returns a dictionary of headers to propagate (e.g., trace headers).
        request_callable: Optional callable to use for making the request.
        **kwargs: Additional keyword arguments to pass to the request callable.

    Returns:
        The response from the HTTP request.
    """
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
    """Requests Session with automatic trace propagation and outbound logs.
    
    Attributes:
        logger: The logger to use for logging request details.
        system: Optional system name for metrics and logging.
        header_getter: Callable that returns a dictionary of headers to propagate (e.g., trace headers).
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        system: str | None = None,
        header_getter: Callable[[], dict[str, str]] = _runtime_outbound_trace_headers,
    ) -> None:
        """Initialise an InstrumentedRequestsSession with trace propagation and logging.
        
        Args:
            logger: The logger to use for logging request details.
            system: Optional system name for metrics and logging.
            header_getter: Callable that returns a dictionary of headers to propagate (e.g., trace headers).
        """
        super().__init__()
        self._logger = logger
        self._system = system
        self._header_getter = header_getter

    def request(  # type: ignore[override]  # intentionally narrows bytes→str internally
        self, method: str | bytes, url: str | bytes, **kwargs: Any
    ) -> requests.Response:  # noqa: D401
        """Override requests.Session.request to add trace propagation and outbound logs.
        
        Args:
            method: The HTTP method to use for the request.
            url: The URL to send the request to.
            **kwargs: Additional keyword arguments to pass to the request.
            
        Returns:
            The response from the HTTP request.
        """
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
        """Override requests.Session.post to add trace propagation and outbound logs.
        
        Args:
            url: The URL to send the POST request to.
            data: The data to include in the body of the POST request.
            json: The JSON data to include in the body of the POST request.
            **kwargs: Additional keyword arguments to pass to the request.
            
        Returns:
            The response from the HTTP request.
        """
        return self.request("POST", url, data=data, json=json, **kwargs)


def sdk_call_with_instrumentation(
    *,
    logger: logging.Logger,
    system: str,
    operation: str,
    call: Callable[[], Any],
    expected_exceptions: tuple[type[BaseException], ...] = (),
) -> Any:
    """Run one SDK call and emit consistent operation-level telemetry.
    
    Args:
        logger: The logger to use for logging SDK call details.
        system: The system name for metrics and logging.
        operation: The operation name for metrics and logging.
        call: The callable representing the SDK call to execute.
        expected_exceptions: A tuple of exception types that are expected and should be handled gracefully.
        
    Returns:
        The result of the SDK call.
    Raises:
        Any exception raised by the SDK call that is not in expected_exceptions.
    """
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
