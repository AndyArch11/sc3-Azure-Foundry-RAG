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

    Parameters
    ----------
    cloud_provider:
        Override the ``CLOUD_PROVIDER`` env var.
    cosmos_container:
        Pre-built Cosmos container client (Azure path).
    table_name:
        DynamoDB table name (AWS path); falls back to ``DYNAMODB_TABLE`` env var.
    dynamo_session:
        ``boto3.Session`` to use (AWS path).
    region_name:
        AWS region (AWS path, ignored when *dynamo_session* is provided).
    """
    provider = (cloud_provider or os.getenv("CLOUD_PROVIDER", "azure")).strip().lower()

    if provider in ("local", "dev"):
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

    raise ValueError(f"Unsupported cloud provider for state store: '{provider}'")
