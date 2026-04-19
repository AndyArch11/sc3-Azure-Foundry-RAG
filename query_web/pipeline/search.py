"""Hybrid search and embedding helpers extracted from app.py."""

from __future__ import annotations

import time
from typing import Any

import requests  # type: ignore[import-untyped]
from azure.search.documents.models import VectorizedQuery


def _embed_query(question: str, *, svc: Any) -> list[float]:
    token = svc._cognitive_token()
    url = (
        f"{svc.config.openai_endpoint}/openai/deployments/"
        f"{svc.config.embedding_deployment}/embeddings?api-version=2023-05-15"
    )
    max_attempts = 4
    base_delay_s = 0.75

    for attempt in range(max_attempts):
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"input": question},
                timeout=30,
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
    """
    timings: dict[str, float] = {}

    if evidence_filter == "__none__":
        timings["embedding_s"] = 0.0
        timings["search_s"] = 0.0
        return [], timings

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

    vector_query = VectorizedQuery(
        vector=vector,
        k_nearest_neighbors=retrieve_k,
        fields="content_vector",
    )

    t1 = time.perf_counter()
    try:
        results = svc.search_client.search(
            search_text=question,
            vector_queries=[vector_query],
            top=retrieve_k,
            filter=evidence_filter,
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
