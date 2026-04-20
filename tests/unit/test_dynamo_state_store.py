"""Unit tests for DynamoDBPollingStateStore and the state_store factory.

All DynamoDB interactions are patched via a lightweight in-memory fake table so
no real AWS credentials or network are required.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.assessment_orchestration.dynamo_state_store import DynamoDBPollingStateStore
from runtime.state_store import get_state_store


# ---------------------------------------------------------------------------
# Fake DynamoDB table
# ---------------------------------------------------------------------------


class _FakeTable:
    """Minimal in-memory DynamoDB Table resource mock."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    def _key(self, source: str, doc_key: str) -> tuple[str, str]:
        return (source, doc_key)

    def get_item(self, *, Key: dict[str, Any]) -> dict[str, Any]:
        item = self._items.get((Key["source"], Key["doc_key"]))
        if item is None:
            return {}
        return {"Item": dict(item)}

    def put_item(self, *, Item: dict[str, Any]) -> None:
        self._items[(Item["source"], Item["doc_key"])] = dict(Item)

    def delete_item(self, *, Key: dict[str, Any]) -> None:
        self._items.pop((Key["source"], Key["doc_key"]), None)

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        source, doc_key = key["source"], key["doc_key"]
        item = dict(self._items.get((source, doc_key)) or {"source": source, "doc_key": doc_key})

        condition = kwargs.get("ConditionExpression", "")
        expr_values = kwargs.get("ExpressionAttributeValues", {})
        expr_names = kwargs.get("ExpressionAttributeNames", {})

        if condition:
            # Very simple evaluator for "#version = :expected_version"
            if "#version = :expected_version" in condition:
                expected = expr_values.get(":expected_version")
                actual = item.get("version")
                if expected != actual:
                    raise _ConditionalCheckFailed("ConditionalCheckFailed")

        # Apply SET expressions
        update_expr = kwargs.get("UpdateExpression", "")
        if update_expr.startswith("SET "):
            parts = update_expr[4:].split(", ")
            for part in parts:
                lhs, rhs = part.split(" = ")
                attr = expr_names.get(lhs.strip(), lhs.strip().lstrip("#"))
                value = expr_values.get(rhs.strip())
                item[attr] = value

        self._items[(source, doc_key)] = item
        return {"Attributes": dict(item)}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        from boto3.dynamodb.conditions import ConditionExpressionBuilder

        key_cond = kwargs.get("KeyConditionExpression")
        filter_expr = kwargs.get("FilterExpression")
        limit = kwargs.get("Limit", 1000)

        builder = ConditionExpressionBuilder()

        source = ""
        prefix = ""
        since_iso = ""

        if key_cond is not None:
            built = builder.build_expression(key_cond)
            values = dict(built.attribute_value_placeholders)
            # Extract source (eq condition on 'source') and prefix (begins_with on 'doc_key')
            for placeholder, val in values.items():
                if isinstance(val, str):
                    if not source:
                        source = val  # first value is the eq on source
                    elif not prefix:
                        prefix = val  # second value is the begins_with prefix

        if filter_expr is not None:
            built_f = builder.build_expression(filter_expr)
            for val in built_f.attribute_value_placeholders.values():
                if isinstance(val, str):
                    since_iso = val
                    break

        results = []
        for (s, dk), item in self._items.items():
            if source and s != source:
                continue
            if prefix and not dk.startswith(prefix):
                continue
            if since_iso:
                ts = item.get("assessed_at") or item.get("last_attempt_at") or ""
                if ts and ts < since_iso:
                    continue
            results.append(dict(item))
            if len(results) >= limit:
                break

        return {"Items": results}


class _ConditionalCheckFailed(Exception):
    pass


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _make_store(table: _FakeTable | None = None) -> DynamoDBPollingStateStore:
    """Return a DynamoDBPollingStateStore backed by a fake table."""
    if table is None:
        table = _FakeTable()
    fake_session = MagicMock()
    fake_dynamo = MagicMock()
    fake_dynamo.Table.return_value = table
    fake_session.resource.return_value = fake_dynamo
    return DynamoDBPollingStateStore("test-table", session=fake_session)


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestDynamoDBPollingStateStoreInit:
    def test_requires_table_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DYNAMODB_TABLE", raising=False)
        with pytest.raises(ValueError, match="table_name"):
            DynamoDBPollingStateStore(session=MagicMock())

    def test_reads_table_name_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNAMODB_TABLE", "my-table")
        store = _make_store()
        assert store._table_name == "test-table"  # explicit arg wins

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNAMODB_TABLE", "env-table")
        fake_session = MagicMock()
        fake_dynamo = MagicMock()
        fake_dynamo.Table.return_value = _FakeTable()
        fake_session.resource.return_value = fake_dynamo
        store = DynamoDBPollingStateStore(session=fake_session)
        assert store._table_name == "env-table"

    def test_missing_boto3_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DYNAMODB_TABLE", "my-table")
        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(RuntimeError, match="boto3"):
                DynamoDBPollingStateStore("my-table")


# ---------------------------------------------------------------------------
# PollingState
# ---------------------------------------------------------------------------


class TestDynamoDBLoadCommitState:
    def test_load_state_returns_default_for_new_source(self) -> None:
        store = _make_store()
        state = store.load_state("confluence")
        assert state.source == "confluence"
        assert state.watermark == ""
        assert state.poll_count == 0

    def test_commit_state_persists_watermark(self) -> None:
        store = _make_store()
        state = store.commit_state("confluence", watermark="2024-01-01T00:00:00Z")
        assert state.watermark == "2024-01-01T00:00:00Z"
        loaded = store.load_state("confluence")
        assert loaded.watermark == "2024-01-01T00:00:00Z"

    def test_commit_state_increments_poll_count(self) -> None:
        store = _make_store()
        store.commit_state("confluence", watermark="w1", poll_count_increment=1)
        store.commit_state("confluence", watermark="w2", poll_count_increment=3)
        state = store.load_state("confluence")
        assert state.poll_count == 4

    def test_commit_state_sets_etag(self) -> None:
        store = _make_store()
        state = store.commit_state("confluence", watermark="w1")
        assert state.etag != ""

    def test_commit_state_with_valid_expected_etag(self) -> None:
        store = _make_store()
        s1 = store.commit_state("src", watermark="w1")
        # version after first commit is 1
        s2 = store.commit_state("src", watermark="w2", expected_etag=s1.etag)
        assert s2.watermark == "w2"

    def test_commit_state_with_wrong_etag_raises(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        store.commit_state("src", watermark="w1")
        with pytest.raises(RuntimeError, match="etag mismatch"):
            store.commit_state("src", watermark="w2", expected_etag="9999")


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


class TestDynamoDBLease:
    def test_acquire_lease_returns_true_when_free(self) -> None:
        store = _make_store()
        assert store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60) is True

    def test_acquire_lease_owner_can_re_acquire(self) -> None:
        store = _make_store()
        store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60)
        assert store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60) is True

    def test_acquire_lease_blocked_by_different_owner(self) -> None:
        store = _make_store()
        store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60)
        assert store.try_acquire_lease("src", owner_run_id="run-2", ttl_seconds=60) is False

    def test_acquire_lease_allowed_after_expiry(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        # Write an expired lease directly
        expired = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        table.put_item(
            Item={
                "source": "src",
                "doc_key": "lock",
                "owner_run_id": "run-old",
                "lease_expires_at": expired,
                "ttl_epoch": 0,
            }
        )
        assert store.try_acquire_lease("src", owner_run_id="run-new", ttl_seconds=60) is True

    def test_renew_lease_returns_true_for_owner(self) -> None:
        store = _make_store()
        store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60)
        assert store.renew_lease("src", owner_run_id="run-1", ttl_seconds=120) is True

    def test_renew_lease_returns_false_for_wrong_owner(self) -> None:
        store = _make_store()
        store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60)
        assert store.renew_lease("src", owner_run_id="run-2", ttl_seconds=120) is False

    def test_renew_lease_returns_false_when_no_lease(self) -> None:
        store = _make_store()
        assert store.renew_lease("src", owner_run_id="run-1", ttl_seconds=60) is False

    def test_release_lease_removes_lock(self) -> None:
        store = _make_store()
        store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60)
        store.release_lease("src", owner_run_id="run-1")
        # After release, another owner should be able to acquire
        assert store.try_acquire_lease("src", owner_run_id="run-2", ttl_seconds=60) is True

    def test_release_lease_noop_for_wrong_owner(self) -> None:
        store = _make_store()
        store.try_acquire_lease("src", owner_run_id="run-1", ttl_seconds=60)
        store.release_lease("src", owner_run_id="run-X")
        # run-1 lock should still be active
        assert store.try_acquire_lease("src", owner_run_id="run-2", ttl_seconds=60) is False


# ---------------------------------------------------------------------------
# Processed event deduplication
# ---------------------------------------------------------------------------


class TestDynamoDBProcessedEvents:
    def test_event_not_processed_by_default(self) -> None:
        store = _make_store()
        assert store.is_event_processed("src", "evt-1") is False

    def test_mark_and_check_processed(self) -> None:
        store = _make_store()
        store.mark_processed_event("src", event_id="evt-1", run_id="run-1")
        assert store.is_event_processed("src", "evt-1") is True

    def test_different_source_is_independent(self) -> None:
        store = _make_store()
        store.mark_processed_event("src-a", event_id="evt-1", run_id="run-1")
        assert store.is_event_processed("src-b", "evt-1") is False

    def test_expired_event_returns_false(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        # Insert item with already-expired ttl_epoch
        table.put_item(
            Item={
                "source": "src",
                "doc_key": "processed:evt-exp",
                "event_id": "evt-exp",
                "run_id": "r1",
                "processed_at": "2020-01-01T00:00:00Z",
                "ttl_epoch": int(time.time()) - 10,  # already expired
            }
        )
        assert store.is_event_processed("src", "evt-exp") is False


# ---------------------------------------------------------------------------
# Failure tracking
# ---------------------------------------------------------------------------


class TestDynamoDBFailureTracking:
    def test_increment_failure_count_returns_count(self) -> None:
        store = _make_store()
        count = store.increment_failure_count(
            "src", event_id="e1", error_message="oops", run_id="r1"
        )
        assert count == 1

    def test_increment_failure_count_accumulates(self) -> None:
        store = _make_store()
        store.increment_failure_count("src", event_id="e1", error_message="a", run_id="r1")
        count = store.increment_failure_count("src", event_id="e1", error_message="b", run_id="r2")
        assert count == 2

    def test_mark_terminal_failure_sets_status(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        store.mark_terminal_failure(
            "src", event_id="e1", error_message="fatal", run_id="r1"
        )
        item = table.get_item(Key={"source": "src", "doc_key": "failure:e1"})["Item"]
        assert item["status"] == "failed_terminal"

    def test_list_recent_failures_filters_by_since_iso(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        # Directly write two failure items: one old, one recent
        table.put_item(
            Item={
                "source": "src",
                "doc_key": "failure:old",
                "event_id": "old",
                "status": "failed_retryable",
                "attempt_count": 1,
                "last_error": "x",
                "last_attempt_at": "2023-01-01T00:00:00Z",
                "run_id": "r1",
            }
        )
        table.put_item(
            Item={
                "source": "src",
                "doc_key": "failure:new",
                "event_id": "new",
                "status": "failed_retryable",
                "attempt_count": 1,
                "last_error": "y",
                "last_attempt_at": "2025-06-01T00:00:00Z",
                "run_id": "r2",
            }
        )
        failures = store.list_recent_failures("src", since_iso="2024-01-01T00:00:00Z")
        ids = [f.event_id for f in failures]
        assert "new" in ids
        assert "old" not in ids


# ---------------------------------------------------------------------------
# Assessment snapshots
# ---------------------------------------------------------------------------


class TestDynamoDBAssessmentSnapshots:
    def test_get_returns_none_when_absent(self) -> None:
        store = _make_store()
        result = store.get_assessment_snapshot("src", target_id="t1", framework_scope="ism")
        assert result is None

    def test_upsert_and_get(self) -> None:
        store = _make_store()
        snap = store.upsert_assessment_snapshot(
            "src",
            target_id="t1",
            framework_scope="ism",
            page_version="v2",
            content_hash="abc",
        )
        assert snap.page_version == "v2"
        assert snap.content_hash == "abc"

        fetched = store.get_assessment_snapshot("src", target_id="t1", framework_scope="ism")
        assert fetched is not None
        assert fetched.page_version == "v2"

    def test_upsert_overwrites(self) -> None:
        store = _make_store()
        store.upsert_assessment_snapshot(
            "src", target_id="t1", framework_scope="ism", page_version="v1", content_hash="h1"
        )
        store.upsert_assessment_snapshot(
            "src", target_id="t1", framework_scope="ism", page_version="v2", content_hash="h2"
        )
        snap = store.get_assessment_snapshot("src", target_id="t1", framework_scope="ism")
        assert snap is not None
        assert snap.page_version == "v2"


# ---------------------------------------------------------------------------
# Poll run summary
# ---------------------------------------------------------------------------


class TestDynamoDBPollRunSummary:
    def test_get_returns_none_when_absent(self) -> None:
        store = _make_store()
        assert store.get_latest_poll_run_summary("src") is None

    def test_upsert_and_get(self) -> None:
        store = _make_store()
        summary = store.upsert_poll_run_summary(
            "src",
            polled_at="2024-06-01T12:00:00Z",
            since_iso="2024-05-01T00:00:00Z",
            watermark="wm1",
            mentions_found=5,
            jobs_queued=3,
            terminal_failures=1,
        )
        assert summary.mentions_found == 5
        assert summary.jobs_queued == 3
        fetched = store.get_latest_poll_run_summary("src")
        assert fetched is not None
        assert fetched.watermark == "wm1"

    def test_negative_counts_clamped_to_zero(self) -> None:
        store = _make_store()
        summary = store.upsert_poll_run_summary(
            "src",
            polled_at="2024-06-01T00:00:00Z",
            since_iso="",
            watermark="",
            mentions_found=-5,
            jobs_queued=-1,
            terminal_failures=-2,
        )
        assert summary.mentions_found == 0
        assert summary.jobs_queued == 0
        assert summary.terminal_failures == 0

    def test_space_keys_preserved(self) -> None:
        store = _make_store()
        summary = store.upsert_poll_run_summary(
            "src",
            polled_at="",
            since_iso="",
            watermark="",
            mentions_found=0,
            jobs_queued=0,
            terminal_failures=0,
            space_keys=("TEAM", "PROJ"),
        )
        assert set(summary.space_keys) == {"TEAM", "PROJ"}


# ---------------------------------------------------------------------------
# Page assessments
# ---------------------------------------------------------------------------


class TestDynamoDBPageAssessments:
    def test_upsert_and_list(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        store.upsert_page_assessment(
            "src",
            target_id="page-1",
            framework_scope="ism",
            title="My Page",
            target_url="https://example.com/page-1",
            space_key="TEAM",
            status="assessed",
            overall_risk="medium",
            findings_count=3,
            assessed_at="2025-01-01T00:00:00Z",
            page_version="v1",
        )
        records = store.list_recent_page_assessments("src", since_iso="2024-01-01T00:00:00Z")
        assert len(records) == 1
        assert records[0].target_id == "page-1"
        assert records[0].findings_count == 3

    def test_list_excludes_old_records(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        store.upsert_page_assessment(
            "src",
            target_id="old-page",
            framework_scope="ism",
            title="Old",
            target_url="https://x.com",
            space_key="S",
            status="assessed",
            overall_risk="low",
            findings_count=0,
            assessed_at="2022-01-01T00:00:00Z",
            page_version="v1",
        )
        records = store.list_recent_page_assessments("src", since_iso="2024-01-01T00:00:00Z")
        assert records == []

    def test_list_respects_limit(self) -> None:
        table = _FakeTable()
        store = _make_store(table)
        for i in range(5):
            store.upsert_page_assessment(
                "src",
                target_id=f"page-{i}",
                framework_scope="ism",
                title=f"Page {i}",
                target_url=f"https://example.com/{i}",
                space_key="S",
                status="assessed",
                overall_risk="low",
                findings_count=i,
                assessed_at=f"2025-0{i+1}-01T00:00:00Z",
                page_version="v1",
            )
        records = store.list_recent_page_assessments("src", since_iso="2024-01-01T00:00:00Z", limit=3)
        assert len(records) <= 3


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


class TestStateStoreFactory:
    def test_default_provider_is_azure_raises_without_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
        with pytest.raises(ValueError, match="cosmos_container"):
            get_state_store()

    def test_azure_provider_returns_cosmos_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        from runtime.assessment_orchestration.state_store import CosmosPollingStateStore

        fake_container = MagicMock()
        store = get_state_store(cosmos_container=fake_container)
        assert isinstance(store, CosmosPollingStateStore)

    def test_local_returns_in_memory_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        from runtime.assessment_orchestration.state_store import InMemoryPollingStateStore

        store = get_state_store()
        assert isinstance(store, InMemoryPollingStateStore)

    def test_dev_alias_returns_in_memory_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "dev")
        from runtime.assessment_orchestration.state_store import InMemoryPollingStateStore

        store = get_state_store()
        assert isinstance(store, InMemoryPollingStateStore)

    def test_aws_returns_dynamodb_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        fake_session = MagicMock()
        fake_dynamo = MagicMock()
        fake_dynamo.Table.return_value = _FakeTable()
        fake_session.resource.return_value = fake_dynamo
        store = get_state_store(table_name="my-table", dynamo_session=fake_session)
        assert isinstance(store, DynamoDBPollingStateStore)

    def test_cloud_provider_argument_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        from runtime.assessment_orchestration.state_store import InMemoryPollingStateStore

        store = get_state_store(cloud_provider="local")
        assert isinstance(store, InMemoryPollingStateStore)

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        with pytest.raises(ValueError, match="Unsupported"):
            get_state_store()
