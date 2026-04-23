"""In-memory search adapter for unit tests and local development."""

from __future__ import annotations

import re
from typing import Any


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
    """Extract meaningful query terms for local substring-style ranking."""
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.lower())
    return [tok for tok in tokens if tok not in _LOCAL_QUERY_STOPWORDS and len(tok) >= 3]


class _SearchResults(list[dict[str, Any]]):
    """List-like search results that expose Azure-style get_count()."""

    def __init__(self, items: list[dict[str, Any]], *, total_count: int | None = None) -> None:
        super().__init__(items)
        self._total_count = total_count

    def get_count(self) -> int | None:
        return self._total_count


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
        query_text: str | None = None,
        top: int,
        vector_query: list[float] | None = None,  # noqa: ARG002 – unused in local impl
        filters: str | None = None,  # noqa: ARG002 – not applied in local impl
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> _SearchResults:
        """Naive substring search across all string fields."""
        if query_text is None and "search_text" in extra_kwargs:
            query_text = str(extra_kwargs.get("search_text") or "")
        if filters is None and "filter" in extra_kwargs:
            filters = str(extra_kwargs.get("filter") or "")
        include_total_count = bool(extra_kwargs.get("include_total_count", False))

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

        # Basic subset of OData filtering used in this repo.
        if filters:
            import re

            m = re.match(r"^\s*([a-zA-Z0-9_]+)\s+eq\s+'([^']*)'\s*$", filters)
            if m:
                field, value = m.group(1), m.group(2)
                matched = [d for d in matched if str(d.get(field, "")) == value]

        results = matched[:top]
        if select:
            results = [{k: doc[k] for k in select if k in doc} for doc in results]
        total = len(matched) if include_total_count else None
        return _SearchResults(results, total_count=total)
