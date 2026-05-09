"""Provider identity helpers shared by provider registries."""

from __future__ import annotations

from typing import Literal

CloudProvider = Literal["azure", "aws", "local"]


def normalise_cloud_provider(value: str | None) -> CloudProvider:
    """Normalise user/env provider values into the canonical provider key."""

    provider = (value or "azure").strip().lower()
    if provider in {"local", "dev"}:
        return "local"
    if provider == "azure":
        return "azure"
    if provider == "aws":
        return "aws"
    raise ValueError(
        f"Unsupported cloud provider '{provider}'. Expected one of: azure, aws, local"
    )
