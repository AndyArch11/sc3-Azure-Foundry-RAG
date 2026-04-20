"""Azure credential provider adapter."""

from __future__ import annotations

from azure.identity import DefaultAzureCredential


class AzureCredentialProvider:
    """Credential provider backed by DefaultAzureCredential."""

    def __init__(self) -> None:
        self._credential = DefaultAzureCredential()

    def get_sdk_credential(self) -> DefaultAzureCredential:
        return self._credential

    def get_provider_name(self) -> str:
        return "azure"
