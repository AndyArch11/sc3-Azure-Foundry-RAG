"""Cloud-agnostic retrieval/search contract."""

from __future__ import annotations

from typing import Any, Protocol


class SearchClient(Protocol):
    """Provider-neutral search interface for retrieval pipelines."""

    @property
    def index_name(self) -> str:
        """Return logical index name."""

    def search(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute search and return normalized documents."""
