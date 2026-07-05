"""Optional Prometheus metrics for outbound HTTP and SDK instrumentation.

``prometheus-client`` is an optional runtime dependency (it is required in
query-web but not declared in the core runtime worker requirements).  This
module wraps metric creation behind a ``try/except ImportError`` so the
runtime worker can import ``outbound_instrumentation`` without
``prometheus-client`` installed.

When the library *is* available, four metric objects are registered once in
the default Prometheus registry and shared across the process. Their labels
mirror the outbound log vocabulary used by instrumentation:

* ``provider``  - transport family (``http`` or ``sdk``)
* ``system``    - canonical outbound dependency name
* ``operation`` - stable operation identifier
* ``status``    - coarse outcome (``ok``/``error``)

HTTP metrics also retain ``method`` and ``status_code`` for protocol-level
breakdown and backward-compatible dashboards.

* ``outbound_http_requests_total``   - Counter, labels: provider, system, method, status, status_code, operation
* ``outbound_http_duration_seconds`` - Histogram, labels: provider, system, operation, status
* ``outbound_sdk_calls_total``       - Counter, labels: provider, system, operation, status
* ``outbound_sdk_duration_seconds``  - Histogram, labels: provider, system, operation, status

When the library is *not* available, lightweight no-op stubs that expose the
same ``labels(...).inc()`` / ``labels(...).observe(v)`` interface are used so
call sites need no conditional guards.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "OUTBOUND_HTTP_REQUESTS_TOTAL",
    "OUTBOUND_HTTP_DURATION_SECONDS",
    "OUTBOUND_SDK_CALLS_TOTAL",
    "OUTBOUND_SDK_DURATION_SECONDS",
    "metrics_available",
]

# ---------------------------------------------------------------------------
# Shared histogram bucket set for sub-10 s outbound calls
# ---------------------------------------------------------------------------
_HTTP_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

OUTBOUND_HTTP_REQUESTS_TOTAL: Any
OUTBOUND_HTTP_DURATION_SECONDS: Any
OUTBOUND_SDK_CALLS_TOTAL: Any
OUTBOUND_SDK_DURATION_SECONDS: Any

try:
    from prometheus_client import Counter, Histogram  # type: ignore[import-untyped]

    OUTBOUND_HTTP_REQUESTS_TOTAL = Counter(
        "outbound_http_requests_total",
        "Total outbound HTTP requests issued by the runtime",
        labelnames=["provider", "system", "method", "status", "status_code", "operation"],
    )

    OUTBOUND_HTTP_DURATION_SECONDS = Histogram(
        "outbound_http_duration_seconds",
        "Duration of outbound HTTP requests in seconds",
        labelnames=["provider", "system", "operation", "status"],
        buckets=_HTTP_BUCKETS,
    )

    OUTBOUND_SDK_CALLS_TOTAL = Counter(
        "outbound_sdk_calls_total",
        "Total outbound SDK (Azure / OpenSearch) calls issued by the runtime",
        labelnames=["provider", "system", "operation", "status"],
    )

    OUTBOUND_SDK_DURATION_SECONDS = Histogram(
        "outbound_sdk_duration_seconds",
        "Duration of outbound SDK calls in seconds",
        labelnames=["provider", "system", "operation", "status"],
        buckets=_HTTP_BUCKETS,
    )

    metrics_available: bool = True

except ImportError:  # pragma: no cover – runtime worker without prometheus-client
    class _NoOpChild:
        """No-op label child that silently absorbs inc/observe calls.
        
        Attributes:
            inc: Method to increment the counter (no-op).
            observe: Method to observe a value in the histogram (no-op).
        """

        def inc(self, amount: float = 1) -> None:  # noqa: D401
            pass

        def observe(self, value: float) -> None:  # noqa: D401
            pass

    class _NoOpMetric:
        """No-op metric that returns a _NoOpChild for any label combination.
        
        Attributes:
            labels: Method to retrieve a _NoOpChild for the specified label values.
        """

        def labels(self, **kwargs: object) -> _NoOpChild:
            return _NoOpChild()

    OUTBOUND_HTTP_REQUESTS_TOTAL = _NoOpMetric()
    OUTBOUND_HTTP_DURATION_SECONDS = _NoOpMetric()
    OUTBOUND_SDK_CALLS_TOTAL = _NoOpMetric()
    OUTBOUND_SDK_DURATION_SECONDS = _NoOpMetric()

    metrics_available = False
