"""Azure AI Search adapter implementing the SearchClient Protocol."""

from __future__ import annotations

import logging
from typing import Any, cast

from azure.search.documents import SearchClient as _AzureSDKSearchClient
from azure.search.documents.models import VectorizedQuery

from runtime.outbound_instrumentation import sdk_call_with_instrumentation

logger = logging.getLogger(__name__)


class _SearchResults(list[dict[str, Any]]):
    """List-like search results with optional total count metadata.

    Attributes:
        _total_count: The total count of matching documents, if available.
    """

    def __init__(self, items: list[dict[str, Any]], total_count: int | None = None) -> None:
        """Initialise search results with optional total count.

        Args:
            items: The list of search result items.
            total_count: Optional total count of matching documents.
        """
        super().__init__(items)
        self._total_count = total_count

    def get_count(self) -> int | None:
        """Return the total count of matching documents, if available.

        Returns:
            The total count of matching documents, or None if not available.
        """
        return self._total_count


class AzureSearchClient:
    """SearchClient backed by Azure AI Search.
    
    Attributes:
        _client: The underlying Azure SDK SearchClient instance.
        _index: The name of the search index.
        _embedding_fn: Optional embedding function for vector queries.
        _embedding_deployment: The deployment name for embedding generation.
    """

    def __init__(
        self,
        endpoint: str,
        index: str,
        credential: Any,
        *,
        embedding_fn: Any = None,
        embedding_deployment: str = "text-embedding-ada-002",
    ) -> None:
        """Initialise an AzureSearchClient instance.

        Args:
            endpoint: The Azure Search service endpoint URL.
            index: The name of the search index to use.
            credential: The credential for authenticating with Azure Search.
            embedding_fn: Optional embedding function for vector queries.
            embedding_deployment: The deployment name for embedding generation.
        """
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
        """Return the name of the search index."""
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
        """Execute a search query against the Azure Search index.

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
            vector_ctor = cast(Any, VectorizedQuery)
            try:
                vectorized_query = vector_ctor(
                    vector=vector_query,
                    k=top,
                    fields="content_vector",
                )
            except TypeError:
                vectorized_query = vector_ctor(
                    vector=vector_query,
                    k_nearest_neighbors=top,
                    fields="content_vector",
                )
            kwargs["vector_queries"] = [
                vectorized_query
            ]

        # Forward provider-specific hints (e.g. query_type, semantic_configuration_name).
        kwargs.update(extra_kwargs)

        search_fn = getattr(self._client, "search", None)
        if not callable(search_fn):
            raise AttributeError("SearchClient.search is unavailable")

        results = sdk_call_with_instrumentation(
            logger=logger,
            system="azure-search",
            operation="search_documents",
            call=lambda: search_fn(**kwargs),  # pylint: disable=not-callable
        )
        total_count = results.get_count() if hasattr(results, "get_count") else None
        items = [dict(r) for r in results]
        return _SearchResults(items=items, total_count=total_count)

    def delete_documents(self, *, documents: list[dict[str, Any]]) -> None:
        """Delete documents via the underlying Azure Search SDK client.

        Args:
            documents: Documents to delete from the index.
        """

        delete_fn = cast(Any, getattr(self._client, "delete_documents", None))
        if not callable(delete_fn):
            raise AttributeError("SearchClient.delete_documents is unavailable")

        delete_fn(documents=documents)

    def load_documents(self, docs: list[dict[str, Any]]) -> None:
        """Unsupported for cloud backends; retained for protocol compatibility.

        Args:
            docs: Documents to load into the index.
        Raises:
            NotImplementedError: Always raised for cloud backends.
        """
        raise NotImplementedError("AzureSearchClient does not support load_documents")
