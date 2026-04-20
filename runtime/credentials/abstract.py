"""Cloud-agnostic credential provider contract."""

from __future__ import annotations

from typing import Any, Protocol


class CredentialProvider(Protocol):
    """Provides provider-specific credential/session objects."""

    def get_sdk_credential(self) -> Any:
        """Return a credential/session object for SDK clients."""

    def get_provider_name(self) -> str:
        """Return provider name used for telemetry and logging."""
