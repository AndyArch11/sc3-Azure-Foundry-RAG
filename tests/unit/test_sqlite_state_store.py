"""Unit tests for SqlitePollingStateStore and SqliteConversationStore.

All tests use in-memory SQLite databases (:memory:) — no file I/O required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from runtime.assessment_orchestration.sqlite_state_store import SqlitePollingStateStore
from query_web.conversation_store import SqliteConversationStore


# ---------------------------------------------------------------------------
# SqlitePollingStateStore
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> SqlitePollingStateStore:
    return SqlitePollingStateStore(":memory:")


class TestSqlitePollingStateStoreBasic:
    def test_load_state_returns_default(self, store: SqlitePollingStateStore) -> None:
        state = store.load_state("confluence")
        assert state.source == "confluence"
        assert state.poll_count == 0
        assert state.watermark == ""

    def test_commit_state_persists(self, store: SqlitePollingStateStore) -> None:
        state = store.commit_state(
            "confluence",
            watermark="2024-01-01T00:00:00",
            poll_count_increment=1,
        )
        assert state.watermark == "2024-01-01T00:00:00"
        assert state.poll_count == 1

        reloaded = store.load_state("confluence")
        assert reloaded.watermark == "2024-01-01T00:00:00"
        assert reloaded.poll_count == 1

    def test_commit_state_accumulates_count(self, store: SqlitePollingStateStore) -> None:
        store.commit_state("confluence", watermark="t1", poll_count_increment=3)
        store.commit_state("confluence", watermark="t2", poll_count_increment=2)
        state = store.load_state("confluence")
        assert state.poll_count == 5

    def test_sources_are_independent(self, store: SqlitePollingStateStore) -> None:
        store.commit_state("confluence", watermark="conf-mark")
        store.commit_state("sharepoint", watermark="sp-mark")
        assert store.load_state("confluence").watermark == "conf-mark"
        assert store.load_state("sharepoint").watermark == "sp-mark"


class TestSqlitePollingStateStoreLease:
    def test_acquire_lease_succeeds(self, store: SqlitePollingStateStore) -> None:
        assert store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=60)

    def test_renew_lease(self, store: SqlitePollingStateStore) -> None:
        store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=60)
        assert store.renew_lease("confluence", owner_run_id="run-1", ttl_seconds=120)

    def test_second_owner_blocked(self, store: SqlitePollingStateStore) -> None:
        store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=3600)
        assert not store.try_acquire_lease("confluence", owner_run_id="run-2", ttl_seconds=60)

    def test_release_allows_reacquire(self, store: SqlitePollingStateStore) -> None:
        store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=60)
        store.release_lease("confluence", owner_run_id="run-1")
        assert store.try_acquire_lease("confluence", owner_run_id="run-2", ttl_seconds=60)

    def test_wrong_owner_release_noop(self, store: SqlitePollingStateStore) -> None:
        store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=60)
        store.release_lease("confluence", owner_run_id="run-WRONG")
        # run-1 still holds the lease
        assert not store.try_acquire_lease("confluence", owner_run_id="run-2", ttl_seconds=60)

    def test_renew_wrong_owner_fails(self, store: SqlitePollingStateStore) -> None:
        store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=60)
        assert not store.renew_lease("confluence", owner_run_id="run-WRONG", ttl_seconds=60)


class TestSqlitePollingStateStoreProcessed:
    def test_mark_and_check_processed(self, store: SqlitePollingStateStore) -> None:
        assert not store.is_event_processed("confluence", "evt-1")
        store.mark_processed_event("confluence", event_id="evt-1", run_id="run-1")
        assert store.is_event_processed("confluence", "evt-1")

    def test_processed_events_are_source_scoped(self, store: SqlitePollingStateStore) -> None:
        store.mark_processed_event("confluence", event_id="evt-1", run_id="run-1")
        assert not store.is_event_processed("sharepoint", "evt-1")

    def test_ttl_expiry_clears_event(self, store: SqlitePollingStateStore) -> None:
        # Mark with an already-expired TTL by setting expires_at in the past directly
        from runtime.assessment_orchestration.state_store import _utc_now_iso
        from datetime import timedelta

        # Inject an expired doc directly
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        payload = {
            "source": "confluence",
            "event_id": "evt-expired",
            "run_id": "run-1",
            "processed_at": _utc_now_iso(),
            "expires_at": past,
        }
        store._upsert("confluence", "confluence:processed:evt-expired", "processed", payload)
        # The TTL check happens on _read; after expiry, is_event_processed should return False
        assert not store.is_event_processed("confluence", "evt-expired")


class TestSqlitePollingStateStoreFailures:
    def test_increment_failure(self, store: SqlitePollingStateStore) -> None:
        count = store.increment_failure_count(
            "confluence", event_id="evt-1", error_message="oops", run_id="run-1"
        )
        assert count == 1
        count2 = store.increment_failure_count(
            "confluence", event_id="evt-1", error_message="again", run_id="run-1"
        )
        assert count2 == 2

    def test_mark_terminal_failure(self, store: SqlitePollingStateStore) -> None:
        store.mark_terminal_failure(
            "confluence", event_id="evt-2", error_message="final", run_id="run-1"
        )
        since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        failures = store.list_recent_failures("confluence", since_iso=since)
        assert any(f.event_id == "evt-2" and f.status == "failed_terminal" for f in failures)

    def test_list_recent_failures_since_filter(self, store: SqlitePollingStateStore) -> None:
        store.mark_terminal_failure(
            "confluence", event_id="evt-3", error_message="msg", run_id="run-1"
        )
        future_since = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        failures = store.list_recent_failures("confluence", since_iso=future_since)
        assert not any(f.event_id == "evt-3" for f in failures)


class TestSqlitePollingStateStoreSnapshot:
    def test_upsert_and_get_snapshot(self, store: SqlitePollingStateStore) -> None:
        snap = store.upsert_assessment_snapshot(
            "confluence",
            target_id="page-123",
            framework_scope="essential_eight",
            page_version="3",
            content_hash="abc123",
        )
        assert snap.target_id == "page-123"
        assert snap.content_hash == "abc123"

        retrieved = store.get_assessment_snapshot(
            "confluence", target_id="page-123", framework_scope="essential_eight"
        )
        assert retrieved is not None
        assert retrieved.page_version == "3"

    def test_missing_snapshot_returns_none(self, store: SqlitePollingStateStore) -> None:
        result = store.get_assessment_snapshot(
            "confluence", target_id="missing", framework_scope="ism"
        )
        assert result is None


class TestSqlitePollingStateStorePollRun:
    def test_upsert_and_get_poll_run(self, store: SqlitePollingStateStore) -> None:
        now = datetime.now(UTC).isoformat()
        summary = store.upsert_poll_run_summary(
            "confluence",
            polled_at=now,
            since_iso=now,
            watermark="wm-1",
            mentions_found=5,
            jobs_queued=3,
            terminal_failures=0,
            space_keys=("ENG", "OPS"),
        )
        assert summary.mentions_found == 5
        assert summary.space_keys == ("ENG", "OPS")

        retrieved = store.get_latest_poll_run_summary("confluence")
        assert retrieved is not None
        assert retrieved.watermark == "wm-1"

    def test_missing_poll_run_returns_none(self, store: SqlitePollingStateStore) -> None:
        assert store.get_latest_poll_run_summary("missing-source") is None


class TestSqlitePollingStateStorePageAssessments:
    def test_upsert_and_list_assessments(self, store: SqlitePollingStateStore) -> None:
        now = datetime.now(UTC).isoformat()
        store.upsert_page_assessment(
            "confluence",
            target_id="page-1",
            framework_scope="essential_eight",
            title="My Page",
            target_url="https://example.com/page-1",
            space_key="ENG",
            status="assessed",
            overall_risk="medium",
            findings_count=2,
            assessed_at=now,
            page_version="1",
        )
        since = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        records = store.list_recent_page_assessments("confluence", since_iso=since)
        assert len(records) == 1
        assert records[0].target_id == "page-1"
        assert records[0].findings_count == 2


# ---------------------------------------------------------------------------
# SqliteConversationStore
# ---------------------------------------------------------------------------


@pytest.fixture()
def conv_store() -> SqliteConversationStore:
    return SqliteConversationStore(":memory:")


class TestSqliteConversationStore:
    def test_upsert_and_read(self, conv_store: SqliteConversationStore) -> None:
        doc = {"id": "user1_conv1", "user_id": "user1", "messages": [], "type": "conversation"}
        conv_store.upsert_item(doc)
        result = conv_store.read_item(item="user1_conv1", partition_key="user1")
        assert result["id"] == "user1_conv1"
        assert result["user_id"] == "user1"

    def test_read_missing_raises_key_error(self, conv_store: SqliteConversationStore) -> None:
        with pytest.raises(KeyError):
            conv_store.read_item(item="nonexistent", partition_key="user1")

    def test_upsert_updates_existing(self, conv_store: SqliteConversationStore) -> None:
        doc = {"id": "user1_conv1", "user_id": "user1", "messages": []}
        conv_store.upsert_item(doc)
        updated = {"id": "user1_conv1", "user_id": "user1", "messages": [{"role": "user", "content": "hi"}]}
        conv_store.upsert_item(updated)
        result = conv_store.read_item(item="user1_conv1", partition_key="user1")
        assert len(result["messages"]) == 1

    def test_upsert_without_id_raises(self, conv_store: SqliteConversationStore) -> None:
        with pytest.raises(ValueError, match="'id'"):
            conv_store.upsert_item({"user_id": "u1"})

    def test_multiple_conversations_independent(self, conv_store: SqliteConversationStore) -> None:
        conv_store.upsert_item({"id": "u1_c1", "user_id": "u1", "data": "alpha"})
        conv_store.upsert_item({"id": "u1_c2", "user_id": "u1", "data": "beta"})
        assert conv_store.read_item(item="u1_c1", partition_key="u1")["data"] == "alpha"
        assert conv_store.read_item(item="u1_c2", partition_key="u1")["data"] == "beta"

    def test_query_items_filters_by_user(self, conv_store: SqliteConversationStore) -> None:
        conv_store.upsert_item(
            {
                "id": "u1_c1",
                "user_id": "u1",
                "conversation_id": "c1",
                "type": "conversation",
                "updated_at": "2026-04-23T10:00:00+00:00",
                "messages": [],
            }
        )
        conv_store.upsert_item(
            {
                "id": "u2_c1",
                "user_id": "u2",
                "conversation_id": "c1",
                "type": "conversation",
                "updated_at": "2026-04-23T10:01:00+00:00",
                "messages": [],
            }
        )
        rows = conv_store.query_items(
            query="SELECT * FROM c WHERE c.user_id = @user_id",
            parameters=[{"name": "@user_id", "value": "u1"}],
        )
        assert len(rows) == 1
        assert rows[0]["user_id"] == "u1"

    def test_query_items_orders_by_updated_at_desc(self, conv_store: SqliteConversationStore) -> None:
        conv_store.upsert_item(
            {
                "id": "u1_old",
                "user_id": "u1",
                "conversation_id": "old",
                "type": "conversation",
                "updated_at": "2026-04-23T09:00:00+00:00",
                "messages": [],
            }
        )
        conv_store.upsert_item(
            {
                "id": "u1_new",
                "user_id": "u1",
                "conversation_id": "new",
                "type": "conversation",
                "updated_at": "2026-04-23T11:00:00+00:00",
                "messages": [],
            }
        )
        rows = conv_store.query_items(
            query="SELECT * FROM c WHERE c.user_id = @user_id ORDER BY c.updated_at DESC",
            parameters=[{"name": "@user_id", "value": "u1"}],
        )
        assert [row["conversation_id"] for row in rows] == ["new", "old"]


# ---------------------------------------------------------------------------
# SqliteConversationStore integration with _load_conversation / _save_conversation
# ---------------------------------------------------------------------------


class TestConversationStoreIntegration:
    """Smoke-test that conversations.py helpers work with SqliteConversationStore."""

    def test_load_creates_new_when_absent(self, conv_store: SqliteConversationStore) -> None:
        from query_web.endpoints.conversations import _load_conversation

        session = _load_conversation("user-abc", "conv-xyz", conv_store)
        assert session.user_id == "user-abc"
        assert session.conversation_id == "conv-xyz"
        assert session.messages == []

    def test_save_and_reload(self, conv_store: SqliteConversationStore) -> None:
        from query_web.endpoints.conversations import (
            ConversationMessage,
            _load_conversation,
            _save_conversation,
        )

        session = _load_conversation("user-abc", "conv-xyz", conv_store)
        session.messages.append(ConversationMessage(role="user", content="hello"))
        _save_conversation(session, conv_store)

        reloaded = _load_conversation("user-abc", "conv-xyz", conv_store)
        assert len(reloaded.messages) == 1
        assert reloaded.messages[0].content == "hello"


# ---------------------------------------------------------------------------
# State store factory
# ---------------------------------------------------------------------------


class TestStateStoreFactory:
    def test_factory_returns_sqlite_when_path_set(self, tmp_path) -> None:
        import os
        from runtime.state_store import get_state_store

        db = str(tmp_path / "test.db")
        store = get_state_store("local", cosmos_container=None)
        # Without LOCAL_STATE_DB_PATH set we get InMemory
        from runtime.assessment_orchestration.state_store import InMemoryPollingStateStore
        assert isinstance(store, InMemoryPollingStateStore)

        # With LOCAL_STATE_DB_PATH set we get SQLite
        os.environ["LOCAL_STATE_DB_PATH"] = db
        try:
            sqlite_store = get_state_store("local")
            assert isinstance(sqlite_store, SqlitePollingStateStore)
        finally:
            del os.environ["LOCAL_STATE_DB_PATH"]

    def test_factory_dev_mode_same_as_local(self, tmp_path) -> None:
        import os
        from runtime.state_store import get_state_store

        db = str(tmp_path / "dev.db")
        os.environ["LOCAL_STATE_DB_PATH"] = db
        try:
            store = get_state_store("dev")
            assert isinstance(store, SqlitePollingStateStore)
        finally:
            del os.environ["LOCAL_STATE_DB_PATH"]
