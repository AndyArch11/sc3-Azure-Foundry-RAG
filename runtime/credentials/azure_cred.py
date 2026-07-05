"""Azure credential provider adapter."""

from __future__ import annotations

from azure.identity import DefaultAzureCredential


class AzureCredentialProvider:
    """Credential provider backed by DefaultAzureCredential."""

    def __init__(self) -> None:
        """Initialise Azure credential provider."""
        self._credential = DefaultAzureCredential()

    def get_sdk_credential(self) -> DefaultAzureCredential:
        """Return a DefaultAzureCredential object for Azure SDK clients."""
        return self._credential

    def get_provider_name(self) -> str:
        """Return provider name used for telemetry and logging."""
        return "azure"
