"""Qdrant-backed local search adapter for Phase A.1 local vector retrieval."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import requests


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

    def _embed_text(self, text: str) -> list[float]:
        payload = {"model": self._embedding_model, "prompt": text}
        response = requests.post(
            f"{self._ollama_base_url}/api/embeddings",
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
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
            vectors.append(self._embed_text(text))
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
        self._client.upsert(collection_name=self._index, points=points)

    def _build_filter(self, filters: str | None) -> Any | None:
        if not filters:
            return None

        m = re.match(r"^\s*([a-zA-Z0-9_]+)\s+eq\s+'([^']*)'\s*$", filters)
        if not m:
            return None

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        return Filter(
            must=[FieldCondition(key=m.group(1), match=MatchValue(value=m.group(2)))]
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
        query_lower = query_text.lower()
        matched: list[dict[str, Any]] = []
        for doc in self._docs:
            text_blob = " ".join(str(v) for v in doc.values()).lower()
            if query_lower in text_blob:
                matched.append(doc)

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
        if effective_query == "*":
            effective_query = ""

        try:
            query_vector = vector_query if vector_query is not None else self._embed_text(effective_query)
            qfilter = self._build_filter(filters)
            result = self._client.search(
                collection_name=self._index,
                query_vector=query_vector,
                limit=max(1, top),
                query_filter=qfilter,
                with_payload=True,
            )
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
