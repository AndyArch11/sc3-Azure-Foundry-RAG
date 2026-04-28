"""Unit tests for Cosmos schema-version stamping and cosmos_schema_access log emission.

Covers:
- ConversationSession.to_dict() stamps schema_version
- _load_conversation logs schema_version_read and upcasted flag
- _save_conversation logs schema_version_written
- CosmosPollingStateStore._upsert stamps schema_version and logs
- CosmosPollingStateStore._read logs schema_version_read and upcasted flag
- CosmosPollingStateStore resolves container name from client.id attribute
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, call

import pytest

from query_web.constants import COSMOS_CONVERSATION_SCHEMA_VERSION, SERVICE_NAME
from query_web.endpoints.conversations import (
    ConversationSession,
    _load_conversation,
    _save_conversation,
)
from runtime.assessment_orchestration.state_store import (
    COSMOS_STATE_SCHEMA_VERSION,
    CosmosPollingStateStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container(*, name: str = "test-container") -> MagicMock:
    container = MagicMock()
    container.id = name
    return container


def _cosmos_doc(schema_version: str | None = None) -> dict:
    doc: dict = {
        "id": "user_1_conv_1",
        "session_id": "sess-1",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "messages": [],
        "response_ratings": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "type": "conversation",
    }
    if schema_version is not None:
        doc["schema_version"] = schema_version
    return doc


# ---------------------------------------------------------------------------
# ConversationSession.to_dict
# ---------------------------------------------------------------------------


class TestConversationSessionSchemaStamp:
    def test_to_dict_includes_schema_version(self) -> None:
        session = ConversationSession(session_id="s", user_id="u", conversation_id="c")
        doc = session.to_dict()
        assert doc["schema_version"] == COSMOS_CONVERSATION_SCHEMA_VERSION

    def test_schema_version_constant_is_v1(self) -> None:
        assert COSMOS_CONVERSATION_SCHEMA_VERSION == "v1"


# ---------------------------------------------------------------------------
# _load_conversation logging
# ---------------------------------------------------------------------------


class TestLoadConversationSchemaLogging:
    def test_load_logs_schema_version_read(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        container.read_item.return_value = _cosmos_doc(schema_version="v1")

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _load_conversation("user-1", "conv-1", container)

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert len(access_records) == 1
        assert access_records[0].__dict__["schema_version_read"] == "v1"
        assert access_records[0].__dict__["operation"] == "read"
        assert access_records[0].__dict__["client_id"] == SERVICE_NAME

    def test_load_logs_upcasted_true_when_schema_is_old(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        container = _make_container()
        container.read_item.return_value = _cosmos_doc(schema_version="v0")

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _load_conversation("user-1", "conv-1", container)

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records[0].__dict__["upcasted"] is True

    def test_load_logs_upcasted_false_when_schema_is_current(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        container = _make_container()
        container.read_item.return_value = _cosmos_doc(
            schema_version=COSMOS_CONVERSATION_SCHEMA_VERSION
        )

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _load_conversation("user-1", "conv-1", container)

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records[0].__dict__["upcasted"] is False

    def test_load_logs_unknown_when_schema_version_absent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        container = _make_container()
        container.read_item.return_value = _cosmos_doc(schema_version=None)

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _load_conversation("user-1", "conv-1", container)

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records[0].__dict__["schema_version_read"] == "unknown"
        assert access_records[0].__dict__["upcasted"] is True

    def test_load_logs_correlation_id_when_provided(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        container.read_item.return_value = _cosmos_doc(schema_version="v1")

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _load_conversation("user-1", "conv-1", container, correlation_id="corr-1")

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records[0].__dict__["correlation_id"] == "corr-1"


# ---------------------------------------------------------------------------
# _save_conversation logging
# ---------------------------------------------------------------------------


class TestSaveConversationSchemaLogging:
    def test_save_logs_schema_version_written(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        session = ConversationSession(session_id="s", user_id="user-1", conversation_id="conv-1")

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _save_conversation(session, container)

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert len(access_records) == 1
        assert (
            access_records[0].__dict__["schema_version_written"]
            == COSMOS_CONVERSATION_SCHEMA_VERSION
        )
        assert access_records[0].__dict__["operation"] == "upsert"
        assert access_records[0].__dict__["client_id"] == SERVICE_NAME
        assert access_records[0].__dict__["upcasted"] is False

    def test_save_no_log_when_container_is_none(self, caplog: pytest.LogCaptureFixture) -> None:
        session = ConversationSession(session_id="s", user_id="user-1", conversation_id="conv-1")

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _save_conversation(session, None)

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records == []

    def test_save_logs_correlation_id_when_provided(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        session = ConversationSession(session_id="s", user_id="user-1", conversation_id="conv-1")

        with caplog.at_level(logging.INFO, logger="query_web.endpoints.conversations"):
            _save_conversation(session, container, correlation_id="corr-2")

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records[0].__dict__["correlation_id"] == "corr-2"


# ---------------------------------------------------------------------------
# CosmosPollingStateStore._upsert
# ---------------------------------------------------------------------------


class TestCosmosStateStoreUpsertSchemaStamp:
    def test_upsert_stamps_schema_version(self) -> None:
        container = _make_container()
        container.upsert_item.return_value = {}
        store = CosmosPollingStateStore(container)

        payload: dict = {"id": "src:state", "source": "confluence"}
        store._upsert(payload)

        saved = container.upsert_item.call_args[0][0]
        assert saved["schema_version"] == COSMOS_STATE_SCHEMA_VERSION

    def test_upsert_does_not_overwrite_existing_schema_version(self) -> None:
        """setdefault must not clobber an already-versioned document."""
        container = _make_container()
        container.upsert_item.return_value = {}
        store = CosmosPollingStateStore(container)

        payload: dict = {"id": "src:state", "source": "confluence", "schema_version": "v0"}
        store._upsert(payload)

        saved = container.upsert_item.call_args[0][0]
        assert saved["schema_version"] == "v0"

    def test_upsert_logs_schema_version_written(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        container.upsert_item.return_value = {}
        store = CosmosPollingStateStore(container)

        with caplog.at_level(logging.INFO, logger="runtime.assessment_orchestration.state_store"):
            store._upsert({"id": "src:state", "source": "confluence"})

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert len(access_records) == 1
        assert access_records[0].__dict__["schema_version_written"] == COSMOS_STATE_SCHEMA_VERSION
        assert access_records[0].__dict__["operation"] == "upsert"


# ---------------------------------------------------------------------------
# CosmosPollingStateStore._read
# ---------------------------------------------------------------------------


class TestCosmosStateStoreReadSchemaLogging:
    def test_read_logs_schema_version_read(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        container.read_item.return_value = {
            "id": "src:state",
            "source": "confluence",
            "schema_version": "v1",
        }
        store = CosmosPollingStateStore(container)

        with caplog.at_level(logging.INFO, logger="runtime.assessment_orchestration.state_store"):
            store._read("confluence", "confluence:state")

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert len(access_records) == 1
        assert access_records[0].__dict__["schema_version_read"] == "v1"
        assert access_records[0].__dict__["operation"] == "read"

    def test_read_logs_upcasted_true_for_old_schema(self, caplog: pytest.LogCaptureFixture) -> None:
        container = _make_container()
        container.read_item.return_value = {"id": "x", "source": "s", "schema_version": "v0"}
        store = CosmosPollingStateStore(container)

        with caplog.at_level(logging.INFO, logger="runtime.assessment_orchestration.state_store"):
            store._read("s", "x")

        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records[0].__dict__["upcasted"] is True

    def test_read_returns_none_on_exception_without_logging(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        container = _make_container()
        container.read_item.side_effect = Exception("not found")
        store = CosmosPollingStateStore(container)

        with caplog.at_level(logging.INFO, logger="runtime.assessment_orchestration.state_store"):
            result = store._read("s", "x")

        assert result is None
        access_records = [r for r in caplog.records if r.getMessage() == "cosmos_schema_access"]
        assert access_records == []


# ---------------------------------------------------------------------------
# Container name resolution
# ---------------------------------------------------------------------------


class TestCosmosStateStoreContainerName:
    def test_container_name_from_client_id_attribute(self) -> None:
        container = _make_container(name="polling-state")
        store = CosmosPollingStateStore(container)
        assert store._container_name == "polling-state"

    def test_container_name_falls_back_when_no_id_attribute(self) -> None:
        container = MagicMock(spec=[])  # no .id attribute
        store = CosmosPollingStateStore(container)
        assert store._container_name == "state-store"
