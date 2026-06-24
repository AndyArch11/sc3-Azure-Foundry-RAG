"""Storage client factory and protocol exports."""

from __future__ import annotations

import importlib
import os
from typing import Any

try:
    _provider_core = importlib.import_module("runtime.provider_core")
except ModuleNotFoundError:
    # In ingestion images modules are copied to /app/* without runtime/ prefix.
    _provider_core = importlib.import_module("provider_core")

DEFAULT_CLOUD_PROVIDER_REGISTRY = _provider_core.DEFAULT_CLOUD_PROVIDER_REGISTRY

from .abstract import StorageClient


def get_storage_client(
    cloud_provider: str | None = None,
    *,
    credential: Any = None,
    account_url: str | None = None,
    region_name: str | None = None,
    session: Any = None,
    base_dir: str | None = None,
) -> StorageClient:
    """Return a provider-appropriate StorageClient."""

    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "azure":
        from .azure_blob import AzureBlobStorageClient

        url = account_url or os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
        if not url:
            raise ValueError(
                "account_url or AZURE_STORAGE_ACCOUNT_URL must be set for Azure storage"
            )
        return AzureBlobStorageClient(account_url=url, credential=credential)

    if provider == "aws":
        from .aws_s3 import AWSS3StorageClient

        return AWSS3StorageClient(region_name=region_name, session=session)

    if provider == "local":
        from .local_file import LocalFileStorageClient

        return LocalFileStorageClient(base_dir=base_dir)

    raise AssertionError(f"Unhandled provider '{provider}'")


__all__ = ["StorageClient", "get_storage_client"]

