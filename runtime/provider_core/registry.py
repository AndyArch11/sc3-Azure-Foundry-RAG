"""Registry for cloud provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocols import CloudProviderAdapter, ProviderCapabilities
from .types import CloudProvider, normalise_cloud_provider


@dataclass(frozen=True)
class _AzureAdapter:
    @property
    def provider(self) -> CloudProvider:
        return "azure"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_embeddings=True,
            supports_semantic_search=True,
            supports_inline_ingestion_trigger=True,
            supports_background_assessment_worker=True,
        )

    def map_search_request(
        self,
        *,
        query_text: str,
        filter_expr: str,
        top: int,
        select: list[str] | None = None,
        include_total_count: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"search_text": query_text, "filter": filter_expr, "top": top}
        if include_total_count:
            kwargs["include_total_count"] = True
        if select is not None:
            kwargs["select"] = select
        return kwargs


@dataclass(frozen=True)
class _AWSAdapter:
    @property
    def provider(self) -> CloudProvider:
        return "aws"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_embeddings=False,
            supports_semantic_search=False,
            supports_inline_ingestion_trigger=True,
            supports_background_assessment_worker=True,
        )

    def map_search_request(
        self,
        *,
        query_text: str,
        filter_expr: str,
        top: int,
        select: list[str] | None = None,
        include_total_count: bool = False,
    ) -> dict[str, Any]:
        del include_total_count  # unsupported by current OpenSearch adapter
        return {
            "query_text": query_text,
            "filters": filter_expr or None,
            "top": top,
            "select": select,
        }


@dataclass(frozen=True)
class _LocalAdapter:
    @property
    def provider(self) -> CloudProvider:
        return "local"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_embeddings=True,
            supports_semantic_search=False,
            supports_inline_ingestion_trigger=False,
            supports_background_assessment_worker=False,
        )

    def map_search_request(
        self,
        *,
        query_text: str,
        filter_expr: str,
        top: int,
        select: list[str] | None = None,
        include_total_count: bool = False,
    ) -> dict[str, Any]:
        del include_total_count
        return {
            "query_text": query_text,
            "filters": filter_expr or None,
            "top": top,
            "select": select,
        }


class CloudProviderRegistry:
    """In-memory registry of cloud provider adapters."""

    def __init__(self) -> None:
        self._providers: dict[CloudProvider, CloudProviderAdapter] = {}

    def register(self, adapter: CloudProviderAdapter) -> None:
        provider = normalise_cloud_provider(adapter.provider)
        if provider in self._providers:
            raise ValueError(f"Provider '{provider}' is already registered")
        self._providers[provider] = adapter

    def get(self, cloud_provider: str | None) -> CloudProviderAdapter:
        provider = normalise_cloud_provider(cloud_provider)
        try:
            return self._providers[provider]
        except KeyError as exc:
            raise ValueError(f"Provider '{provider}' is not registered") from exc

    def providers(self) -> tuple[CloudProvider, ...]:
        return tuple(sorted(self._providers.keys()))


def build_default_registry() -> CloudProviderRegistry:
    """Create a registry pre-loaded with built-in provider adapters."""

    registry = CloudProviderRegistry()
    registry.register(_AzureAdapter())
    registry.register(_AWSAdapter())
    registry.register(_LocalAdapter())
    return registry


DEFAULT_CLOUD_PROVIDER_REGISTRY = build_default_registry()
