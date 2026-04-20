"""Search client factory and protocol exports."""

from __future__ import annotations

import os
from typing import Any

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

    provider = (cloud_provider or os.getenv("CLOUD_PROVIDER", "azure")).strip().lower()

    if provider == "azure":
        from .azure_search import AzureSearchClient

        ep = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT", "")
        idx = index_name or os.getenv("AZURE_SEARCH_INDEX", "")
        if not ep:
            raise ValueError("endpoint or AZURE_SEARCH_ENDPOINT must be set for Azure search")
        if not idx:
            raise ValueError("index_name or AZURE_SEARCH_INDEX must be set for Azure search")
        return AzureSearchClient(endpoint=ep, index=idx, credential=credential)

    if provider == "aws":
        from .opensearch import AWSOpenSearchClient

        ep = endpoint or os.getenv("OPENSEARCH_ENDPOINT", "")
        idx = index_name or os.getenv("OPENSEARCH_INDEX", "")
        return AWSOpenSearchClient(endpoint=ep, index=idx, region_name=region_name)

    if provider in {"local", "dev"}:
        from .local_inmemory import LocalInMemorySearchClient

        idx = index_name or "local-index"
        return LocalInMemorySearchClient(index=idx, documents=documents)

    raise ValueError(
        f"Unsupported cloud provider '{provider}'. Expected one of: azure, aws, local"
    )


__all__ = ["SearchClient", "get_search_client"]

