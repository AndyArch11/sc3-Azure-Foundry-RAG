"""In-memory search adapter for unit tests and local development."""

from __future__ import annotations

from typing import Any


class LocalInMemorySearchClient:
    """SearchClient backed by a pre-loaded list of documents.

    Useful for tests and offline development. Performs naive full-text
    substring match and ignores vector queries.
    """

    def __init__(self, index: str, documents: list[dict[str, Any]] | None = None) -> None:
        self._index = index
        self._docs: list[dict[str, Any]] = list(documents or [])

    @property
    def index_name(self) -> str:
        return self._index

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Replace the in-memory document set."""
        self._docs = list(docs)

    def search(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,  # noqa: ARG002 – unused in local impl
        filters: str | None = None,  # noqa: ARG002 – not applied in local impl
        select: list[str] | None = None,
        **extra_kwargs: Any,  # noqa: ARG002 – provider hints ignored locally
    ) -> list[dict[str, Any]]:
        """Naive substring search across all string fields."""
        query_lower = query_text.lower()
        matched: list[dict[str, Any]] = []
        for doc in self._docs:
            text_blob = " ".join(str(v) for v in doc.values()).lower()
            if query_lower in text_blob:
                matched.append(doc)
        results = matched[:top]
        if select:
            results = [{k: doc[k] for k in select if k in doc} for doc in results]
        return results
