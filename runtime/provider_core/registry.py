"""Registry for cloud provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocols import CloudProviderAdapter, ProviderCapabilities
from .types import CloudProvider, normalise_cloud_provider


@dataclass(frozen=True)
class _AzureAdapter:
    """Adapter for Azure-specific behaviour behind registry dispatch.
    
    Attributes:
        provider: The canonical provider key.
        capabilities: The provider capability matrix.
    """
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
        """Map neutral search inputs into provider-specific client kwargs.
        
        Args:
            query_text: The search query text.
            filter_expr: The search filter expression.
            top: The maximum number of results to return.
            select: Optional list of fields to include in the results.
            include_total_count: Whether to include the total count of matching documents.
        Returns:
            A dictionary of provider-specific keyword arguments for the search client."""
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
        """Map neutral search inputs into provider-specific client kwargs.

        Args:
            query_text: The search query text.
            filter_expr: The search filter expression.
            top: The maximum number of results to return.
            select: Optional list of fields to include in the results.
            include_total_count: Whether to include the total count of matching documents.
        Returns:
            A dictionary of provider-specific keyword arguments for the search client."""
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
        """Map neutral search inputs into provider-specific client kwargs.

        Args:
            query_text: The search query text.
            filter_expr: The search filter expression.
            top: The maximum number of results to return.
            select: Optional list of fields to include in the results.
            include_total_count: Whether to include the total count of matching documents.
        Returns:
            A dictionary of provider-specific keyword arguments for the search client."""
        del include_total_count
        return {
            "query_text": query_text,
            "filters": filter_expr or None,
            "top": top,
            "select": select,
        }


class CloudProviderRegistry:
    """In-memory registry of cloud provider adapters.
    
    Attributes:
        _providers: A mapping of normalised cloud provider keys to their adapters.
    """

    def __init__(self) -> None:
        """Initialise an empty registry of cloud provider adapters."""
        self._providers: dict[CloudProvider, CloudProviderAdapter] = {}

    def register(self, adapter: CloudProviderAdapter) -> None:
        """Register a cloud provider adapter in the registry.

        Args:
            adapter: The CloudProviderAdapter instance to register.
        """
        provider = normalise_cloud_provider(adapter.provider)
        if provider in self._providers:
            raise ValueError(f"Provider '{provider}' is already registered")
        self._providers[provider] = adapter

    def get(self, cloud_provider: str | None) -> CloudProviderAdapter:
        """Retrieve a cloud provider adapter from the registry.

        Args:
            cloud_provider: The name of the cloud provider to retrieve.
        Returns:
            The CloudProviderAdapter instance for the specified provider.
        Raises:
            ValueError: If the provider is not registered.
        """
        provider = normalise_cloud_provider(cloud_provider)
        try:
            return self._providers[provider]
        except KeyError as exc:
            raise ValueError(f"Provider '{provider}' is not registered") from exc

    def providers(self) -> tuple[CloudProvider, ...]:
        """Retrieve a tuple of all registered cloud providers.

        Returns:
            A tuple of registered cloud provider keys.
        """
        return tuple(sorted(self._providers.keys()))


def build_default_registry() -> CloudProviderRegistry:
    """Create a registry pre-loaded with built-in provider adapters.
    
    Returns:
        A CloudProviderRegistry instance with default adapters registered.
    """

    registry = CloudProviderRegistry()
    registry.register(_AzureAdapter())
    registry.register(_AWSAdapter())
    registry.register(_LocalAdapter())
    return registry


DEFAULT_CLOUD_PROVIDER_REGISTRY = build_default_registry()
