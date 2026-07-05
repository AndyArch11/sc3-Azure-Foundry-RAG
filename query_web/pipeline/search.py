"""Hybrid search and embedding helpers extracted from app.py."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests  # type: ignore[import-untyped]

from query_web.request_context import outbound_trace_headers
from runtime.outbound_instrumentation import request_with_instrumentation
from runtime.provider_core import DEFAULT_CLOUD_PROVIDER_REGISTRY

logger = logging.getLogger(__name__)


def _resolve_provider_adapter(cloud_provider: str | None):
    """Resolve cloud provider adapter with a safe Azure fallback.

    Args:
        cloud_provider: The raw cloud provider string (e.g., "aws", "azure", "gcp").

    Returns:
        The corresponding provider adapter from the DEFAULT_CLOUD_PROVIDER_REGISTRY.
    """

    try:
        return DEFAULT_CLOUD_PROVIDER_REGISTRY.get(cloud_provider)
    except ValueError:
        return DEFAULT_CLOUD_PROVIDER_REGISTRY.get("azure")


def _is_missing_index_error(exc: Exception) -> bool:
    """Determine if the exception indicates that the search index is missing.

    Args:
        exc: The exception raised during the search operation.

    Returns:
        True if the exception indicates a missing index, False otherwise.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return isinstance(exc, requests.exceptions.HTTPError) and status_code == 404


def _client_search(
    client: Any,
    *,
    query_text: str = "*",
    filter_expr: str = "",
    top: int,
    select: list[str] | None = None,
    include_total_count: bool = False,
    cloud_provider: str | None = None,
) -> Any:
    """Dispatch client.search() with provider-appropriate keyword arguments.

    Uses provider-core adapter mapping so this function remains provider-neutral.

    Args:
        client: The search client instance to use for the search operation.
        query_text: The search query text (default is "*").
        filter_expr: The filter expression for the search (default is "").
        top: The maximum number of results to return.
        select: Optional list of fields to select in the search results.
        include_total_count: Whether to include the total count of matching documents (default is False).
        cloud_provider: Optional cloud provider string to override the default (default is None).

    Returns:
        The search results from the client.search() call.
    """

    provider_raw = cloud_provider
    if provider_raw is None:
        provider_raw = os.getenv("CLOUD_PROVIDER")

    adapter = _resolve_provider_adapter(provider_raw)

    kwargs = adapter.map_search_request(
        query_text=query_text,
        filter_expr=filter_expr,
        top=top,
        select=select,
        include_total_count=include_total_count,
    )
    return client.search(**kwargs)


def _embed_query(question: str, *, svc: Any) -> list[float]:
    """Embed the query text using the configured embedding model.

    Args:
        question: The query text to embed.
        svc: The service object providing access to configuration and logging.
    Returns:
        A list of floats representing the embedding vector for the query text.
    """
    provider_raw = (
        str(getattr(getattr(svc, "config", None), "cloud_provider", "") or "").strip().lower()
    )
    if not provider_raw:
        provider_raw = os.getenv("CLOUD_PROVIDER") or ""

    adapter = _resolve_provider_adapter(provider_raw)
    if adapter.provider == "aws":
        from runtime.llm.bedrock import bedrock_embed_text

        model_id = str(
            getattr(getattr(svc, "config", None), "embedding_deployment", "")
            or os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "")
        ).strip()
        if not model_id:
            raise RuntimeError("BEDROCK_EMBEDDING_MODEL_ID is required for AWS embeddings")

        region_name = (os.getenv("AWS_REGION") or "").strip() or None
        return bedrock_embed_text(question, model_id=model_id, region_name=region_name)

    token = svc._cognitive_token()
    url = (
        f"{svc.config.openai_endpoint}/openai/deployments/"
        f"{svc.config.embedding_deployment}/embeddings?api-version=2023-05-15"
    )
    max_attempts = 4
    base_delay_s = 0.75

    for attempt in range(max_attempts):
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **outbound_trace_headers(),
            }
            response = request_with_instrumentation(
                "POST",
                url,
                logger=svc.logger,
                headers=headers,
                json={"input": question},
                timeout=30,
                retry_count=attempt,
                system="azure-openai",
                operation="embedding_create",
                header_getter=outbound_trace_headers,
                request_callable=requests.post,
            )
            response.raise_for_status()
            payload = response.json()
            return payload["data"][0]["embedding"]
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status_code in {429, 500, 502, 503, 504}
            if attempt >= max_attempts - 1 or not retryable:
                raise

            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
            try:
                delay_s = max(float(retry_after), 0.0) if retry_after else 0.0
            except (TypeError, ValueError):
                delay_s = 0.0
            if delay_s <= 0:
                delay_s = base_delay_s * (2**attempt)

            svc.logger.warning(
                "Embedding request failed with status %s (attempt %d/%d); retrying in %.2fs",
                status_code,
                attempt + 1,
                max_attempts,
                delay_s,
            )
            time.sleep(delay_s)

    raise RuntimeError("Embedding request failed after retries")


def _hybrid_search(
    question: str,
    retrieve_k: int,
    evidence_filter: str | None = None,
    *,
    svc: Any,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Hybrid search over documents.

    This path is resilient: if the grounding-index does not exist yet (e.g., ingestion
    not yet run), it returns an empty result set rather than failing the query.

    Args:
        question: The query text to search for.
        retrieve_k: The maximum number of results to retrieve.
        evidence_filter: Optional filter expression to apply to the search.
        svc: The service object providing access to configuration and logging.

    Returns:
        A tuple containing:
            - A list of dictionaries representing the search results.
            - A dictionary of timing metrics for embedding and search operations.
    """
    timings: dict[str, float] = {}

    if evidence_filter == "__none__":
        timings["embedding_s"] = 0.0
        timings["search_s"] = 0.0
        return [], timings

    provider_raw = (
        str(getattr(getattr(svc, "config", None), "cloud_provider", "") or "").strip().lower()
    )
    if not provider_raw:
        provider_raw = os.getenv("CLOUD_PROVIDER") or ""

    adapter = _resolve_provider_adapter(provider_raw)
    should_embed = (
        adapter.capabilities.supports_embeddings and adapter.capabilities.supports_semantic_search
    )

    if not should_embed:
        vector = None
        timings["embedding_s"] = 0.0
    else:
        t0 = time.perf_counter()
        try:
            vector = svc._embed_query(question)
        except Exception as exc:
            timings["embedding_s"] = round(time.perf_counter() - t0, 3)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429:
                timings["embedding_rate_limited"] = 1.0
            svc.logger.warning("Embedding failed; returning empty hybrid results: %s", exc)
            timings["search_s"] = 0.0
            return [], timings

        timings["embedding_s"] = round(time.perf_counter() - t0, 3)

    t1 = time.perf_counter()
    try:
        results = svc.search_client.search(
            query_text=question,
            top=retrieve_k,
            vector_query=vector,
            filters=evidence_filter,
            select=[
                "content",
                "source_name",
                "source_path",
                "corpus",
                "corpus_role",
                "upload_source",
                "uploaded_by",
                "upload_batch",
                "uploaded_at",
                "original_filename",
                "content_sha256",
                "normalised_text_sha256",
                "dedupe_hash",
                "dedupe_method",
            ],
        )
        items: list[dict[str, Any]] = []
        for r in results:
            score = r.get("@search.score")
            items.append(
                {
                    "content": (r.get("content") or "").strip(),
                    "source_name": r.get("source_name") or "unknown",
                    "source_path": r.get("source_path") or "",
                    "corpus": (r.get("corpus") or "").strip().lower(),
                    "corpus_role": (r.get("corpus_role") or "").strip().lower(),
                    "upload_source": r.get("upload_source") or "",
                    "uploaded_by": r.get("uploaded_by") or "",
                    "upload_batch": r.get("upload_batch") or "",
                    "uploaded_at": r.get("uploaded_at") or "",
                    "original_filename": r.get("original_filename") or "",
                    "content_sha256": r.get("content_sha256") or "",
                    "normalised_text_sha256": r.get("normalised_text_sha256") or "",
                    "dedupe_hash": r.get("dedupe_hash") or "",
                    "dedupe_method": r.get("dedupe_method") or "",
                    "score": float(score) if score is not None else 0.0,
                }
            )
    except Exception:
        # Grounding-index may not exist if document ingestion hasn't run yet.
        # Gracefully return empty results so query can proceed with controls-only.
        items = []

    timings["search_s"] = round(time.perf_counter() - t1, 3)
    return items, timings


# ---------------------------------------------------------------------------
# Search index document helpers (moved from app.py)
# ---------------------------------------------------------------------------


def _delete_search_documents_by_filter(
    client: Any,
    *,
    filter_expr: str,
    key_field: str,
    page_size: int = 500,
    max_rounds: int = 50,
) -> dict[str, int]:
    """Delete documents from the search index matching the specified filter expression.

    Args:
        client: The search client instance to use for the delete operation.
        filter_expr: The filter expression to identify documents for deletion.
        key_field: The field name used as the unique identifier for documents in the index.
        page_size: The maximum number of documents to retrieve per page (default is 500).
        max_rounds: The maximum number of rounds to attempt deletion (default is 50).

    Returns:
        A dictionary containing the number of deleted documents and the number of rounds attempted.
    """
    deleted = 0
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        try:
            pager = _client_search(
                client,
                filter_expr=filter_expr,
                top=page_size,
                select=[key_field],
            )
        except Exception as exc:
            if _is_missing_index_error(exc):
                logger.info(
                    "Search index missing while deleting documents for filter %s; treating as empty",
                    filter_expr,
                )
                break
            raise
        keys: list[str] = []
        for item in pager:
            value = str(item.get(key_field, "")).strip()
            if value:
                keys.append(value)
        if not keys:
            break
        client.delete_documents(documents=[{key_field: key} for key in keys])
        deleted += len(keys)
        if len(keys) < page_size:
            break
    return {"deleted": deleted, "rounds": rounds}


def _count_search_documents_by_filter(
    client: Any,
    *,
    filter_expr: str,
) -> dict[str, int]:
    """Count documents in the search index matching the specified filter expression.

    Args:
        client: The search client instance to use for the count operation.
        filter_expr: The filter expression to identify documents for counting.
    Returns:
        A dictionary containing the number of documents that would be deleted based on the filter expression.
    """
    try:
        pager = _client_search(
            client,
            filter_expr=filter_expr,
            top=1,
            include_total_count=True,
        )
        for _ in pager:
            break
        count = pager.get_count() or 0
        return {"would_delete": int(count)}
    except Exception as exc:
        if _is_missing_index_error(exc):
            logger.info(
                "Search index missing while counting documents for filter %s; treating as empty",
                filter_expr,
            )
            return {"would_delete": 0}
        raise


def _list_search_documents_by_filter(
    client: Any,
    *,
    filter_expr: str,
    select_fields: list[str],
    limit: int,
) -> dict[str, Any]:
    """List documents in the search index matching the specified filter expression.

    Args:
        client: The search client instance to use for the list operation.
        filter_expr: The filter expression to identify documents for listing.
        select_fields: The list of fields to include in the returned documents.
        limit: The maximum number of documents to return (capped at 200).
    Returns:
        A dictionary containing the total count, returned count, and the list of items.
    """
    capped_limit = max(1, min(limit, 200))
    try:
        pager = _client_search(
            client,
            filter_expr=filter_expr,
            top=capped_limit,
            include_total_count=True,
            select=select_fields,
        )
    except Exception as exc:
        if _is_missing_index_error(exc):
            logger.info(
                "Search index missing while listing documents for filter %s; treating as empty",
                filter_expr,
            )
            return {
                "total_count": 0,
                "returned_count": 0,
                "items": [],
            }
        raise
    items: list[dict[str, Any]] = []
    for item in pager:
        row: dict[str, Any] = {}
        for field in select_fields:
            row[field] = item.get(field)
        items.append(row)
    count = pager.get_count() or len(items)
    return {
        "total_count": int(count),
        "returned_count": len(items),
        "items": items,
    }


def _count_search_documents_total_by_filter(client: Any, *, filter_expr: str) -> int:
    """Count total documents in the search index matching the specified filter expression.

    Args:
        client: The search client instance to use for the count operation.
        filter_expr: The filter expression to identify documents for counting.
    Returns:
        The total number of documents matching the filter expression.
    """
    try:
        pager = _client_search(
            client,
            filter_expr=filter_expr,
            top=1,
            include_total_count=True,
            select=["id"],
        )
        for _ in pager:
            break
        return int(pager.get_count() or 0)
    except Exception as exc:
        if _is_missing_index_error(exc):
            logger.info(
                "Search index missing while counting total documents for filter %s; treating as empty",
                filter_expr,
            )
            return 0
        logger.warning("Failed to count search documents for filter %s: %s", filter_expr, exc)
        return 0
