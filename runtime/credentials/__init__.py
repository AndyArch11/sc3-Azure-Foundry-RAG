"""Credential provider factory."""

from __future__ import annotations

import os

from .abstract import CredentialProvider
from .aws_cred import AWSCredentialProvider
from .azure_cred import AzureCredentialProvider
from .local_cred import LocalCredentialProvider


def get_credential_provider(cloud_provider: str | None = None) -> CredentialProvider:
    """Return provider adapter for the requested cloud."""

    provider = (cloud_provider or os.getenv("CLOUD_PROVIDER", "azure")).strip().lower()

    if provider == "azure":
        return AzureCredentialProvider()

    if provider == "aws":
        return AWSCredentialProvider(
            profile_name=os.getenv("AWS_PROFILE"),
            region_name=os.getenv("AWS_REGION"),
        )

    if provider in {"local", "dev"}:
        return LocalCredentialProvider()

    raise ValueError(
        f"Unsupported cloud provider '{provider}'. Expected one of: azure, aws, local"
    )


__all__ = ["CredentialProvider", "get_credential_provider"]
