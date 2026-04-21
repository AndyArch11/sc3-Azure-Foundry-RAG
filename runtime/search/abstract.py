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
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Execute search and return normalized documents.

        Parameters
        ----------
        query_text:
            Full-text query string.
        top:
            Maximum number of results to return.
        vector_query:
            Pre-computed embedding vector for k-NN / hybrid search.
        filters:
            Provider filter expression (OData for Azure, Lucene for OpenSearch).
        select:
            Field names to include in results.  ``None`` returns all fields.
        **extra_kwargs:
            Provider-specific hints forwarded transparently to the underlying
            client.  Unknown kwargs are silently ignored by non-Azure backends.
            Example: ``query_type="semantic"``,
            ``semantic_configuration_name="controls-semantic"``.
        """

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Load documents into the search index (for local backends only).

        This method is only implemented by local search clients (in-memory and Qdrant).
        Azure and AWS search clients do not support this operation.

        Parameters
        ----------
        docs : list[dict[str, Any]]
            Documents to load into the index.
        """
