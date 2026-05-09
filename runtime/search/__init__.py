"""Search client factory and protocol exports."""

from __future__ import annotations

import os
from typing import Any, cast

from runtime.provider_core import DEFAULT_CLOUD_PROVIDER_REGISTRY

from .abstract import SearchClient


def get_search_client(
    cloud_provider: str | None = None,
    *,
    credential: Any = None,
    endpoint: str | None = None,
    index_name: str | None = None,
    region_name: str | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> SearchClient:
    """Return a provider-appropriate SearchClient."""

    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "azure":
        from .azure_search import AzureSearchClient

        ep = endpoint if endpoint is not None else os.getenv("AZURE_SEARCH_ENDPOINT")
        idx = index_name if index_name is not None else os.getenv("AZURE_SEARCH_INDEX")
        if not ep:
            raise ValueError("endpoint or AZURE_SEARCH_ENDPOINT must be set for Azure search")
        if not idx:
            raise ValueError("index_name or AZURE_SEARCH_INDEX must be set for Azure search")
        return cast(SearchClient, AzureSearchClient(endpoint=ep, index=idx, credential=credential))

    if provider == "aws":
        from .opensearch import AWSOpenSearchClient

        ep = endpoint if endpoint is not None else os.getenv("OPENSEARCH_ENDPOINT")
        idx = index_name if index_name is not None else os.getenv("OPENSEARCH_INDEX")
        if not ep:
            raise ValueError("endpoint or OPENSEARCH_ENDPOINT must be set for AWS search")
        if not idx:
            raise ValueError("index_name or OPENSEARCH_INDEX must be set for AWS search")
        return cast(
            SearchClient,
            AWSOpenSearchClient(endpoint=ep, index=idx, region_name=region_name),
        )

    if provider == "local":
        backend = os.getenv("LOCAL_VECTOR_BACKEND", "inmemory").strip().lower()
        if backend == "qdrant":
            try:
                from .local_qdrant import LocalQdrantSearchClient

                idx = index_name or "local-index"
                return LocalQdrantSearchClient(index=idx)
            except ImportError:
                # Keep local profile usable even when optional qdrant dependency
                # is not yet installed in the active Python environment.
                pass

        from .local_inmemory import LocalInMemorySearchClient

        idx = index_name or "local-index"
        return LocalInMemorySearchClient(index=idx, documents=documents)

    raise AssertionError(f"Unhandled provider '{provider}'")


__all__ = ["SearchClient", "get_search_client"]

