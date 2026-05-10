"""Qdrant-backed local search adapter for Phase A.1 local vector retrieval."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Iterable
from typing import Any, cast

logger = logging.getLogger(__name__)

_LOCAL_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def _query_tokens(query: str) -> list[str]:
    """Extract meaningful query terms for local fallback ranking."""

    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.lower())
    return [tok for tok in tokens if tok not in _LOCAL_QUERY_STOPWORDS and len(tok) >= 3]

import requests

from runtime.trace_context import outbound_trace_headers


class _SearchResults(list[dict[str, Any]]):
    """List-like search results that expose Azure-style get_count()."""

    def __init__(self, items: list[dict[str, Any]], *, total_count: int | None = None) -> None:
        super().__init__(items)
        self._total_count = total_count

    def get_count(self) -> int | None:
        return self._total_count


class LocalQdrantSearchClient:
    """SearchClient backed by Qdrant using Ollama embeddings."""

    def __init__(
        self,
        index: str,
        *,
        qdrant_url: str | None = None,
        ollama_base_url: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self._index = index
        env_qdrant_url = os.getenv("QDRANT_URL")
        env_ollama_url = os.getenv("OLLAMA_BASE_URL")
        env_embedding_model = os.getenv("OLLAMA_EMBEDDING_MODEL")
        self._qdrant_url = (qdrant_url or env_qdrant_url or "http://localhost:6333").rstrip("/")
        self._ollama_base_url = (
            ollama_base_url or env_ollama_url or "http://localhost:11434"
        ).rstrip("/")
        self._embedding_model = (embedding_model or env_embedding_model or "nomic-embed-text").strip()
        self._docs: list[dict[str, Any]] = []

        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=self._qdrant_url)

    @property
    def index_name(self) -> str:
        return self._index

    def _text_for_embedding(self, doc: dict[str, Any]) -> str:
        return str(
            doc.get("content")
            or doc.get("requirement_text")
            or doc.get("guidance_text")
            or ""
        ).strip()

    def _point_id(self, doc: dict[str, Any], ordinal: int) -> int:
        seed = str(doc.get("id") or doc.get("chunk_id") or f"{self._index}:{ordinal}")
        return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:15], 16)

    @staticmethod
    def _embedding_char_budget() -> int:
        """Return the initial per-request text budget for Ollama embeddings."""
        raw = os.getenv("OLLAMA_EMBED_MAX_CHARS", "6000").strip()
        try:
            budget = int(raw)
        except ValueError:
            budget = 6000
        return max(256, budget)

    def _embed_text(self, text: str) -> list[float]:
        prompt = text.strip()
        if not prompt:
            raise RuntimeError("Cannot embed empty text")

        min_chars = 256
        prompt = prompt[: self._embedding_char_budget()]

        while True:
            payload = {"model": self._embedding_model, "prompt": prompt}
            response = requests.post(
                f"{self._ollama_base_url}/api/embeddings",
                json=payload,
                timeout=45,
                headers=outbound_trace_headers(),
            )
            try:
                response.raise_for_status()
            except requests.HTTPError:
                body_text = (getattr(response, "text", "") or "").lower()
                context_overflow = (
                    "input length exceeds the context length" in body_text
                    or "context length" in body_text
                    or "llm embedding error" in body_text
                )
                if context_overflow and len(prompt) > min_chars:
                    prompt = prompt[: max(min_chars, len(prompt) // 2)]
                    continue
                raise

            body = response.json()
            if isinstance(body.get("embedding"), list):
                return [float(v) for v in body["embedding"]]
            if isinstance(body.get("embeddings"), list) and body["embeddings"]:
                return [float(v) for v in body["embeddings"][0]]
            raise RuntimeError("Ollama embedding response did not include vectors")

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        from qdrant_client.models import Distance, PointStruct, VectorParams

        self._docs = list(docs)
        if not self._docs:
            return

        vectors: list[list[float]] = []
        payload_docs: list[dict[str, Any]] = []
        for doc in self._docs:
            text = self._text_for_embedding(doc)
            if not text:
                continue
            try:
                vec = self._embed_text(text)
            except Exception as exc:
                logger.warning(
                    "Skipping document — embedding failed (%s): %.120s", type(exc).__name__, exc
                )
                continue
            vectors.append(vec)
            payload_docs.append(doc)

        if not vectors:
            return

        dim = len(vectors[0])
        if self._client.collection_exists(self._index):
            self._client.delete_collection(self._index)
        self._client.create_collection(
            collection_name=self._index,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

        points = [
            PointStruct(id=self._point_id(doc, i), vector=vec, payload=doc)
            for i, (doc, vec) in enumerate(zip(payload_docs, vectors))
        ]
        _UPSERT_BATCH = 64
        for start in range(0, len(points), _UPSERT_BATCH):
            self._client.upsert(
                collection_name=self._index, points=points[start : start + _UPSERT_BATCH]
            )

    def delete_documents(self, *, documents: list[dict[str, Any]]) -> None:
        """Delete documents by payload key/value selectors.

        Accepts the Azure Search-style payload shape used by this codebase,
        e.g. ``[{"id": "..."}]`` or ``[{"requirement_id": "..."}]``.
        """
        selectors: list[dict[str, str]] = []
        for item in documents:
            if not isinstance(item, dict):
                continue
            selector = {
                str(k): str(v)
                for k, v in item.items()
                if str(k).strip() and v is not None and str(v).strip()
            }
            if selector:
                selectors.append(selector)

        if not selectors:
            return

        def _matches(doc: dict[str, Any], selector: dict[str, str]) -> bool:
            return all(str(doc.get(k, "")) == v for k, v in selector.items())

        self._docs = [
            doc for doc in self._docs if not any(_matches(doc, selector) for selector in selectors)
        ]

        # Best-effort qdrant delete by payload filter; local in-memory state above
        # is the source of truth for fallback search and counting.
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            for selector in selectors:
                conditions = [
                    FieldCondition(key=key, match=MatchValue(value=value))
                    for key, value in selector.items()
                ]
                self._client.delete(
                    collection_name=self._index,
                    points_selector=Filter(must=cast(Any, conditions)),
                )
        except Exception:
            pass

    def _build_filter(self, filters: str | None) -> Any | None:
        if not filters:
            return None

        m = re.match(r"^\s*([a-zA-Z0-9_]+)\s+eq\s+'([^']*)'\s*$", filters)
        if not m:
            return None

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        return Filter(
            must=cast(
                Any,
                [FieldCondition(key=m.group(1), match=MatchValue(value=m.group(2)))],
            )
        )

    def _fallback_text_search(
        self,
        *,
        query_text: str,
        top: int,
        filters: str | None,
        select: list[str] | None,
        include_total_count: bool,
    ) -> _SearchResults:
        effective_query = str(query_text or "").strip()
        if effective_query == "*":
            matched = list(self._docs)
        else:
            query_lower = effective_query.lower()
            tokens = _query_tokens(query_lower)
            scored: list[tuple[int, dict[str, Any]]] = []
            for doc in self._docs:
                text_blob = " ".join(str(v) for v in doc.values()).lower()
                if query_lower and query_lower in text_blob:
                    score = 10_000
                elif tokens:
                    score = sum(1 for token in tokens if token in text_blob)
                else:
                    score = 0

                if score > 0:
                    scored.append((score, doc))

            scored.sort(key=lambda item: item[0], reverse=True)
            matched = [doc for _, doc in scored]

        if filters:
            m = re.match(r"^\s*([a-zA-Z0-9_]+)\s+eq\s+'([^']*)'\s*$", filters)
            if m:
                field, value = m.group(1), m.group(2)
                matched = [d for d in matched if str(d.get(field, "")) == value]

        items = matched[:top]
        if select:
            items = [{k: d[k] for k in select if k in d} for d in items]
        total = len(matched) if include_total_count else None
        return _SearchResults(items, total_count=total)

    def search(
        self,
        *,
        query_text: str | None = None,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> _SearchResults:
        if query_text is None and "search_text" in extra_kwargs:
            query_text = str(extra_kwargs.get("search_text") or "")
        if filters is None and "filter" in extra_kwargs:
            filters = str(extra_kwargs.get("filter") or "")
        include_total_count = bool(extra_kwargs.get("include_total_count", False))
        effective_query = str(query_text or "").strip()
        
        # Handle wildcard query: fall back to text search for "*"
        if effective_query == "*":
            return self._fallback_text_search(
                query_text=effective_query,
                top=top,
                filters=filters,
                select=select,
                include_total_count=include_total_count,
            )

        try:
            query_vector = vector_query if vector_query is not None else self._embed_text(effective_query)
            qfilter = self._build_filter(filters)
            search_fn = cast(Any, getattr(self._client, "search", None))
            if not callable(search_fn):
                raise AttributeError("QdrantClient.search is unavailable")

            result = search_fn(  # pylint: disable=not-callable
                collection_name=self._index,
                query_vector=query_vector,
                limit=max(1, top),
                query_filter=qfilter,
                with_payload=True,
            )
            if not isinstance(result, Iterable):
                raise TypeError("Qdrant search result is not iterable")

            items: list[dict[str, Any]] = []
            for point in result:
                payload = dict(point.payload or {})
                payload["@search.score"] = float(getattr(point, "score", 0.0) or 0.0)
                if select:
                    payload = {k: payload[k] for k in select if k in payload}
                items.append(payload)
            total = len(items) if include_total_count else None
            return _SearchResults(items, total_count=total)
        except Exception:
            # Keep local UX resilient when qdrant/ollama are temporarily unavailable.
            return self._fallback_text_search(
                query_text=effective_query,
                top=top,
                filters=filters,
                select=select,
                include_total_count=include_total_count,
            )
