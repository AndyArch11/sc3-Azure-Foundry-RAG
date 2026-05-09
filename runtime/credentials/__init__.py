"""Credential provider factory."""

from __future__ import annotations

import os

from runtime.provider_core import DEFAULT_CLOUD_PROVIDER_REGISTRY

from .abstract import CredentialProvider
from .aws_cred import AWSCredentialProvider
from .azure_cred import AzureCredentialProvider
from .local_cred import LocalCredentialProvider


def get_credential_provider(cloud_provider: str | None = None) -> CredentialProvider:
    """Return provider adapter for the requested cloud."""

    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "azure":
        return AzureCredentialProvider()

    if provider == "aws":
        return AWSCredentialProvider(
            profile_name=os.getenv("AWS_PROFILE"),
            region_name=os.getenv("AWS_REGION"),
        )

    if provider == "local":
        return LocalCredentialProvider()

    raise AssertionError(f"Unhandled provider '{provider}'")


__all__ = ["CredentialProvider", "get_credential_provider"]
