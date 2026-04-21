"""Azure AI Search adapter implementing the SearchClient Protocol."""

from __future__ import annotations

from typing import Any

from azure.search.documents import SearchClient as _AzureSDKSearchClient
from azure.search.documents.models import VectorizedQuery


class _SearchResults(list[dict[str, Any]]):
    """List-like search results with optional total count metadata."""

    def __init__(self, items: list[dict[str, Any]], total_count: int | None = None) -> None:
        super().__init__(items)
        self._total_count = total_count

    def get_count(self) -> int | None:
        return self._total_count


class AzureSearchClient:
    """SearchClient backed by Azure AI Search."""

    def __init__(
        self,
        endpoint: str,
        index: str,
        credential: Any,
        *,
        embedding_fn: Any = None,
        embedding_deployment: str = "text-embedding-ada-002",
    ) -> None:
        self._client = _AzureSDKSearchClient(
            endpoint=endpoint,
            index_name=index,
            credential=credential,
        )
        self._index = index
        self._embedding_fn = embedding_fn
        self._embedding_deployment = embedding_deployment

    @property
    def index_name(self) -> str:
        return self._index

    def search(
        self,
        *,
        query_text: str | None = None,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
        # Backward compatibility with older call sites that pass Azure SDK kwargs
        # directly (search_text/filter) instead of provider-neutral names.
        if query_text is None:
            legacy_query = extra_kwargs.pop("search_text", None)
            if legacy_query is None:
                raise TypeError(
                    "AzureSearchClient.search() missing required keyword argument: "
                    "'query_text' (or legacy 'search_text')"
                )
            query_text = str(legacy_query)

        if filters is None and "filter" in extra_kwargs:
            filters = extra_kwargs.pop("filter")

        kwargs: dict[str, Any] = {
            "search_text": query_text,
            "top": top,
        }
        if filters:
            kwargs["filter"] = filters
        if select:
            kwargs["select"] = select

        if vector_query is not None:
            kwargs["vector_queries"] = [
                VectorizedQuery(
                    vector=vector_query,
                    k=top,
                    fields="content_vector",
                )
            ]

        # Forward provider-specific hints (e.g. query_type, semantic_configuration_name).
        kwargs.update(extra_kwargs)

        results = self._client.search(**kwargs)
        total_count = results.get_count() if hasattr(results, "get_count") else None
        items = [dict(r) for r in results]
        return _SearchResults(items=items, total_count=total_count)

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Unsupported for cloud backends; retained for protocol compatibility."""
        raise NotImplementedError("AzureSearchClient does not support load_documents")
