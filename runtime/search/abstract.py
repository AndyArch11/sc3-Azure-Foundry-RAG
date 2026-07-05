"""Cloud-agnostic retrieval/search contract."""

from __future__ import annotations

from typing import Any, Protocol


class SearchClient(Protocol):
    """Provider-neutral search interface for retrieval pipelines."""

    @property
    def index_name(self) -> str:
        """Return logical index name."""
        ...

    def search(
        self,
        *,
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Execute search and return normalised documents.

        Args:
            query_text: The search query text.
            top: The maximum number of results to return.
            vector_query: Optional vector query for semantic search.
            filters: Optional filter expression for search.
            select: Optional list of fields to include in the results.
            extra_kwargs: Additional provider-specific keyword arguments.
        Returns:
            A list of documents matching the search criteria, each represented as a dictionary.
        """
        ...

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Load documents into the search index (for local backends only).

        This method is only implemented by local search clients (in-memory and Qdrant).
        Azure and AWS search clients do not support this operation.

        Args:
            docs: Documents to load into the index.
        """
        ...

    def delete_documents(self, *, documents: list[dict[str, Any]]) -> None:
        """Delete documents from the search index.

        Args:
            documents: Documents to delete from the index.
        """
        ...
