"""Local credential provider adapter for development and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocalCredential:
    """Marker object for local/no-auth client setups."""

    provider: str = "local"


class LocalCredentialProvider:
    """Credential provider used for local runtime paths.
    
    This provider is used for local development and testing, where no actual cloud credentials are required.
    """

    def __init__(self) -> None:
        """Initialise local credential provider."""
        self._credential = LocalCredential()

    def get_sdk_credential(self) -> LocalCredential:
        """Return a LocalCredential object for local SDK clients."""
        return self._credential

    def get_provider_name(self) -> str:
        """Return provider name used for telemetry and logging."""
        return "local"
