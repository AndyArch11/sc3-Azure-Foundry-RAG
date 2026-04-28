"""Prometheus metrics registry for query-web.

All metric objects are module-level singletons so they are registered once and
shared across the application.  Import ``observe_rag_metrics`` from the RAG
pipeline and ``observe_cosmos_schema_access`` from Cosmos access paths.

The ``/metrics`` HTTP route is registered in ``app.py`` via
``register_metrics_endpoint``.
"""

from __future__ import annotations

from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

# ---------------------------------------------------------------------------
# RAG pipeline latency histograms
# ---------------------------------------------------------------------------

RAG_REQUEST_DURATION = Histogram(
    "rag_request_duration_seconds",
    "End-to-end RAG request latency including LLM, retrieval, and evaluation",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

RETRIEVAL_DURATION = Histogram(
    "rag_retrieval_duration_seconds",
    "Search + embedding latency (rag_retrieval_s)",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

CONTROLS_SEARCH_DURATION = Histogram(
    "rag_controls_search_duration_seconds",
    "Controls corpus search latency (controls_search_s)",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

LLM_DURATION = Histogram(
    "llm_request_duration_seconds",
    "LLM completion latency including optional retry (llm_total_s)",
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

EVALUATOR_DURATION = Histogram(
    "llm_evaluator_duration_seconds",
    "LLM evaluator call latency (evaluator_s)",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ---------------------------------------------------------------------------
# RAG request counters
# ---------------------------------------------------------------------------

RAG_REQUESTS_TOTAL = Counter(
    "rag_requests_total",
    "Total RAG requests processed",
)

RAG_RETRY_TOTAL = Counter(
    "rag_llm_retries_total",
    "Total RAG requests that triggered a quality-retry LLM call",
)

RAG_ERRORS_TOTAL = Counter(
    "rag_errors_total",
    "Total RAG requests that raised an unhandled exception",
    labelnames=["error_type"],
)

# ---------------------------------------------------------------------------
# Cosmos schema version monitoring
# Backs the Log Analytics KQL queries described in the schema evolution runbook.
# ---------------------------------------------------------------------------

COSMOS_SCHEMA_VERSION_READS = Counter(
    "cosmos_schema_version_reads_total",
    "Cosmos documents read, labelled by schema version (for deprecation monitoring)",
    labelnames=["schema_version", "container", "service"],
)

COSMOS_SCHEMA_VERSION_WRITES = Counter(
    "cosmos_schema_version_writes_total",
    "Cosmos documents written, labelled by schema version",
    labelnames=["schema_version", "container", "service"],
)

COSMOS_SCHEMA_UPCASTS = Counter(
    "cosmos_schema_upcasts_total",
    "Cosmos documents that required an upcast on read (old schema version found)",
    labelnames=["container", "service"],
)

# ---------------------------------------------------------------------------
# Trace/correlation header hygiene counters
# Backs alerting for misconfigured upstreams, spec version drift, and
# log-injection attack detection.
# ---------------------------------------------------------------------------

TRACE_DROPPED_TOTAL = Counter(
    "trace_traceparent_dropped_total",
    "Inbound traceparent headers that failed W3C validation and were dropped",
    labelnames=["reason"],
    # reason values: 'malformed', 'all_zeros', 'reserved_version'
)

TRACE_SALVAGED_TOTAL = Counter(
    "trace_traceparent_salvaged_total",
    "Inbound traceparent headers from an unknown future version that were re-emitted as v00",
)

CORRELATION_ID_SANITISED_TOTAL = Counter(
    "trace_correlation_id_sanitised_total",
    "Inbound x-correlation-id values that contained unsafe characters and were sanitised",
)

# ---------------------------------------------------------------------------
# HTTP request duration histogram (all endpoints, labelled by path + method)
# Required for golden-signal latency graphs in the local compose dashboard.
# ---------------------------------------------------------------------------

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Inbound HTTP request latency, labelled by path and method",
    labelnames=["method", "path", "status"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


# ---------------------------------------------------------------------------
# Public helpers called from other modules
# ---------------------------------------------------------------------------


def observe_rag_metrics(metrics: dict[str, float], *, iterations: int = 1) -> None:
    """Record all timing values from a completed RAG pipeline run.

    ``metrics`` is the dict returned under the ``"metrics"`` key by
    ``rag_pipeline._run_rag``.  Call this once per request after the pipeline
    returns successfully.
    """
    RAG_REQUESTS_TOTAL.inc()
    if iterations >= 3:
        RAG_RETRY_TOTAL.inc()

    if (v := metrics.get("total_s")) is not None:
        RAG_REQUEST_DURATION.observe(v)
    if (v := metrics.get("rag_retrieval_s")) is not None:
        RETRIEVAL_DURATION.observe(v)
    if (v := metrics.get("controls_search_s")) is not None:
        CONTROLS_SEARCH_DURATION.observe(v)
    if (v := metrics.get("llm_total_s")) is not None:
        LLM_DURATION.observe(v)
    if (v := metrics.get("evaluator_s")) is not None:
        EVALUATOR_DURATION.observe(v)


def observe_cosmos_schema_access(
    *,
    operation: str,
    container: str,
    schema_version_read: str,
    schema_version_written: str,
    upcasted: bool,
    service: str,
) -> None:
    """Increment Cosmos schema version counters.

    Called from ``_log_cosmos_access`` helpers so both the structured log line
    and the Prometheus counter are updated in one place.
    """
    if operation in ("read", "upsert", "get") and schema_version_read:
        COSMOS_SCHEMA_VERSION_READS.labels(
            schema_version=schema_version_read,
            container=container,
            service=service,
        ).inc()
        if upcasted:
            COSMOS_SCHEMA_UPCASTS.labels(container=container, service=service).inc()

    if operation in ("write", "upsert") and schema_version_written:
        COSMOS_SCHEMA_VERSION_WRITES.labels(
            schema_version=schema_version_written,
            container=container,
            service=service,
        ).inc()


# ---------------------------------------------------------------------------
# FastAPI route handler
# ---------------------------------------------------------------------------


def metrics_endpoint() -> Response:
    """Render the Prometheus text exposition format for scraping."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def register_metrics_endpoint(app: object) -> None:
    """Add the ``GET /metrics`` route to a FastAPI application."""
    # Import here to avoid a circular import; FastAPI is not imported at
    # module level so this file stays importable in tests without the full app.
    from fastapi import FastAPI as _FastAPI  # noqa: PLC0415

    if isinstance(app, _FastAPI):
        app.add_api_route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False)
