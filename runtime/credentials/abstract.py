"""Cloud-agnostic credential provider contract."""

from __future__ import annotations

from typing import Any, Protocol


class CredentialProvider(Protocol):
    """Provides provider-specific credential/session objects.
    
    Attributes:
        get_sdk_credential: Method to retrieve a credential/session object for SDK clients.
        get_provider_name: Method to retrieve the provider name used for telemetry and logging.
    """

    def get_sdk_credential(self) -> Any:
        """Return a credential/session object for SDK clients."""
        ...

    def get_provider_name(self) -> str:
        """Return provider name used for telemetry and logging."""
        ...
