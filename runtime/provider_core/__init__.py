"""Core provider abstractions and registry for multi-cloud extension."""

from .config_resolution import (
    ProviderResolvedSettings,
    QueryWebProviderSettings,
    parse_framework_authority_order,
    resolve_provider_settings,
    resolve_query_web_provider_settings,
)
from .protocols import CloudProviderAdapter, ProviderCapabilities
from .registry import CloudProviderRegistry, DEFAULT_CLOUD_PROVIDER_REGISTRY, build_default_registry
from .types import CloudProvider, normalise_cloud_provider

__all__ = [
    "CloudProvider",
    "CloudProviderAdapter",
    "CloudProviderRegistry",
    "DEFAULT_CLOUD_PROVIDER_REGISTRY",
    "parse_framework_authority_order",
    "ProviderResolvedSettings",
    "ProviderCapabilities",
    "QueryWebProviderSettings",
    "build_default_registry",
    "normalise_cloud_provider",
    "resolve_query_web_provider_settings",
    "resolve_provider_settings",
]
