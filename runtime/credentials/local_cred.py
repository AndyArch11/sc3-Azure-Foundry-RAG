"""Local credential provider adapter for development and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocalCredential:
    """Marker object for local/no-auth client setups."""

    provider: str = "local"


class LocalCredentialProvider:
    """Credential provider used for local runtime paths."""

    def __init__(self) -> None:
        self._credential = LocalCredential()

    def get_sdk_credential(self) -> LocalCredential:
        return self._credential

    def get_provider_name(self) -> str:
        return "local"
