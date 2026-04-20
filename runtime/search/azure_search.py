"""Azure AI Search adapter implementing the SearchClient Protocol."""

from __future__ import annotations

from typing import Any

from azure.search.documents import SearchClient as _AzureSDKSearchClient
from azure.search.documents.models import VectorizedQuery


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
        query_text: str,
        top: int,
        vector_query: list[float] | None = None,
        filters: str | None = None,
        select: list[str] | None = None,
        **extra_kwargs: Any,
    ) -> list[dict[str, Any]]:
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
        return [dict(r) for r in results]
