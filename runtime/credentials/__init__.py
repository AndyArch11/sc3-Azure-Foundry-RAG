"""Credential provider factory."""

from __future__ import annotations

import importlib
import os

try:
    _provider_core = importlib.import_module("runtime.provider_core")
except ModuleNotFoundError:
    # In ingestion images modules are copied to /app/* without runtime/ prefix.
    _provider_core = importlib.import_module("provider_core")

DEFAULT_CLOUD_PROVIDER_REGISTRY = _provider_core.DEFAULT_CLOUD_PROVIDER_REGISTRY

from .abstract import CredentialProvider


def get_credential_provider(cloud_provider: str | None = None) -> CredentialProvider:
    """Return provider adapter for the requested cloud.
    
    Args:
        cloud_provider: The cloud provider to get the credential provider for.
    Returns:
        The credential provider for the requested cloud.
    Raises:
        AssertionError: If the cloud provider is not supported.
    """

    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "azure":
        from .azure_cred import AzureCredentialProvider

        return AzureCredentialProvider()

    if provider == "aws":
        from .aws_cred import AWSCredentialProvider

        return AWSCredentialProvider(
            profile_name=os.getenv("AWS_PROFILE"),
            region_name=os.getenv("AWS_REGION"),
        )

    if provider == "local":
        from .local_cred import LocalCredentialProvider

        return LocalCredentialProvider()

    raise AssertionError(f"Unhandled provider '{provider}'")


__all__ = ["CredentialProvider", "get_credential_provider"]
