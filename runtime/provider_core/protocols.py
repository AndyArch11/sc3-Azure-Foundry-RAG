"""Protocols and capability model for cloud provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .types import CloudProvider


@dataclass(frozen=True)
class ProviderCapabilities:
    """Feature switches exposed by a provider adapter.
    
    Attributes:
        supports_embeddings: Whether the provider supports embedding generation.
        supports_semantic_search: Whether the provider supports semantic search.
        supports_inline_ingestion_trigger: Whether the provider supports inline ingestion triggers.
        supports_background_assessment_worker: Whether the provider supports background assessment workers.
    """

    supports_embeddings: bool
    supports_semantic_search: bool
    supports_inline_ingestion_trigger: bool
    supports_background_assessment_worker: bool


class CloudProviderAdapter(Protocol):
    """Adapter contract for provider-specific behaviour behind registry dispatch.
    
    Attributes:
        provider: The canonical provider key.
        capabilities: The provider capability matrix.
    """

    @property
    def provider(self) -> CloudProvider:
        """Canonical provider key."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return provider capability matrix."""
        ...

    def map_search_request(
        self,
        *,
        query_text: str,
        filter_expr: str,
        top: int,
        select: list[str] | None = None,
        include_total_count: bool = False,
    ) -> dict[str, Any]:
        """Map neutral search inputs into provider-specific client kwargs.
        
        Args:
            query_text: The search query text.
            filter_expr: The search filter expression.
            top: The maximum number of results to return.
            select: Optional list of fields to include in the results.
            include_total_count: Whether to include the total count of matching documents.
        Returns:
            A dictionary of provider-specific keyword arguments for the search client."""
        ...
