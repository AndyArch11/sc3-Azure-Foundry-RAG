"""AWS credential provider adapter."""

from __future__ import annotations

from typing import Any


class AWSCredentialProvider:
    """Credential provider backed by boto3 Session."""

    def __init__(self, profile_name: str | None = None, region_name: str | None = None) -> None:
        """Initialise AWS credential provider.
        
        Args:
            profile_name: The AWS profile name to use.
            region_name: The AWS region name to use.
        """
        self._profile_name = profile_name
        self._region_name = region_name

    def get_sdk_credential(self) -> Any:
        """Return a boto3 Session object for AWS SDK clients."""
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on optional dependency
            raise RuntimeError(
                "boto3 is required for AWS credential provider but is not installed"
            ) from exc

        return boto3.Session(profile_name=self._profile_name, region_name=self._region_name)

    def get_provider_name(self) -> str:
        """Return provider name used for telemetry and logging."""
        return "aws"
