"""State store abstraction package.

The source-of-truth protocol lives in runtime/assessment_orchestration/state_store.py.

Factory
-------
``get_state_store()`` dispatches on the ``CLOUD_PROVIDER`` env var (or the
``cloud_provider`` argument) and returns a ready-to-use ``PollingStateStore``
implementation:

  * ``azure`` / unset  → ``CosmosPollingStateStore``
  * ``aws``            → ``DynamoDBPollingStateStore``
  * ``local`` / ``dev``→ ``SqlitePollingStateStore`` when ``LOCAL_STATE_DB_PATH`` is set,
                         otherwise ``InMemoryPollingStateStore`` (ephemeral)
"""

from __future__ import annotations

import os
from typing import Any

from runtime.provider_core import DEFAULT_CLOUD_PROVIDER_REGISTRY
from runtime.assessment_orchestration.state_store import (
    CosmosPollingStateStore,
    InMemoryPollingStateStore,
    PollingStateStore,
)


def get_state_store(
    cloud_provider: str | None = None,
    *,
    # Azure / Cosmos kwargs
    cosmos_container: Any = None,
    # AWS / DynamoDB kwargs
    table_name: str | None = None,
    dynamo_session: Any = None,
    region_name: str | None = None,
) -> PollingStateStore:
    """Return a ``PollingStateStore`` for the configured cloud provider.

    Args:
        cloud_provider: Optional name of the cloud provider ("azure", "aws", or "local"). If not provided, the function will attempt to read from the "CLOUD_PROVIDER" environment variable.
        cosmos_container: Optional Cosmos DB container for Azure state store.
        table_name: Optional DynamoDB table name for AWS state store.
        dynamo_session: Optional DynamoDB session for AWS state store.
        region_name: Optional AWS region name for DynamoDB state store.
    Returns:
        An instance of a PollingStateStore appropriate for the specified cloud provider.
    Raises:
        AssertionError: If the specified cloud provider is not supported or required parameters are missing.
    """
    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "local":
        db_path = os.getenv("LOCAL_STATE_DB_PATH", "").strip()
        if db_path:
            from runtime.assessment_orchestration.sqlite_state_store import (
                SqlitePollingStateStore,
            )

            return SqlitePollingStateStore(db_path)
        return InMemoryPollingStateStore()

    if provider == "aws":
        from runtime.assessment_orchestration.dynamo_state_store import (
            DynamoDBPollingStateStore,
        )

        return DynamoDBPollingStateStore(
            table_name=table_name,
            session=dynamo_session,
            region_name=region_name,
        )

    if provider == "azure":
        if cosmos_container is None:
            raise ValueError(
                "cosmos_container must be supplied for the azure state store provider"
            )
        return CosmosPollingStateStore(cosmos_container)

    raise AssertionError(f"Unhandled provider '{provider}'")
