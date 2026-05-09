"""Protocols and capability model for cloud provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .types import CloudProvider


@dataclass(frozen=True)
class ProviderCapabilities:
    """Feature switches exposed by a provider adapter."""

    supports_embeddings: bool
    supports_semantic_search: bool
    supports_inline_ingestion_trigger: bool
    supports_background_assessment_worker: bool


class CloudProviderAdapter(Protocol):
    """Adapter contract for provider-specific behaviour behind registry dispatch."""

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
        """Map neutral search inputs into provider-specific client kwargs."""
        ...
