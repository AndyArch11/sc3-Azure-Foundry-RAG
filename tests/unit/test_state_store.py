from __future__ import annotations

from datetime import UTC, datetime, timedelta

from runtime.assessment_orchestration.state_store import (CosmosPollingStateStore,
                                                          InMemoryPollingStateStore)


class _FakeContainer:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self.fail_delete = False

    def read_item(self, *, item: str, partition_key: str):
        key = (partition_key, item)
        if key not in self._items:
            raise KeyError("not found")
        return dict(self._items[key])

    def upsert_item(self, payload: dict):
        key = (str(payload.get("source") or ""), str(payload.get("id") or ""))
        current = dict(payload)
        etag = int(self._items.get(key, {}).get("_etag") or 0) + 1
        current["_etag"] = str(etag)
        self._items[key] = current
        return dict(current)

    def delete_item(self, *, item: str, partition_key: str):
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self._items.pop((partition_key, item), None)


def test_inmemory_store_core_flows() -> None:
    store = InMemoryPollingStateStore()

    state0 = store.load_state("confluence")
    assert state0.source == "confluence"
    assert state0.poll_count == 0

    state1 = store.commit_state(
        "confluence",
        watermark="2026-01-01T00:00:00+00:00",
        last_processed_event_id="evt-1",
        last_error={"x": 1},
        poll_count_increment=2,
    )
    assert state1.poll_count == 2
    assert state1.last_processed_event_id == "evt-1"

    assert store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=30) is True
    assert store.try_acquire_lease("confluence", owner_run_id="run-2", ttl_seconds=30) is False
    assert store.renew_lease("confluence", owner_run_id="run-2", ttl_seconds=30) is False
    assert store.renew_lease("confluence", owner_run_id="run-1", ttl_seconds=30) is True
    store.release_lease("confluence", owner_run_id="run-2")
    store.release_lease("confluence", owner_run_id="run-1")

    assert store.is_event_processed("confluence", "evt-2") is False
    store.mark_processed_event("confluence", event_id="evt-2", run_id="run-1", ttl_hours=1)
    assert store.is_event_processed("confluence", "evt-2") is True

    # Expiry branch
    store._processed[("confluence", "evt-expired")] = {
        "source": "confluence",
        "event_id": "evt-expired",
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    assert store.is_event_processed("confluence", "evt-expired") is False

    attempts = store.increment_failure_count(
        "confluence", event_id="evt-3", error_message="oops", run_id="run-1"
    )
    assert attempts == 1
    attempts = store.increment_failure_count(
        "confluence", event_id="evt-3", error_message="oops2", run_id="run-1"
    )
    assert attempts == 2
    store.mark_terminal_failure(
        "confluence", event_id="evt-3", error_message="fatal", run_id="run-1"
    )

    assert (
        store.get_assessment_snapshot("confluence", target_id="t1", framework_scope="NIST CSF")
        is None
    )
    snapshot = store.upsert_assessment_snapshot(
        "confluence",
        target_id="t1",
        framework_scope="NIST CSF",
        page_version="v1",
        content_hash="abc",
    )
    assert snapshot.target_id == "t1"
    loaded = store.get_assessment_snapshot("confluence", target_id="t1", framework_scope="NIST CSF")
    assert loaded is not None
    assert loaded.page_version == "v1"


def test_cosmos_store_core_flows_and_delete_failure() -> None:
    container = _FakeContainer()
    store = CosmosPollingStateStore(container)

    state0 = store.load_state("confluence")
    assert state0.poll_count == 0

    state1 = store.commit_state(
        "confluence",
        watermark="2026-01-01T00:00:00+00:00",
        last_processed_event_id="evt-1",
        poll_count_increment=1,
    )
    assert state1.poll_count == 1

    assert store.try_acquire_lease("confluence", owner_run_id="run-1", ttl_seconds=30) is True
    assert store.try_acquire_lease("confluence", owner_run_id="run-2", ttl_seconds=30) is False
    assert store.renew_lease("confluence", owner_run_id="run-2", ttl_seconds=30) is False
    assert store.renew_lease("confluence", owner_run_id="run-1", ttl_seconds=30) is True

    container.fail_delete = True
    store.release_lease("confluence", owner_run_id="run-1")
    container.fail_delete = False
    store.release_lease("confluence", owner_run_id="run-1")

    assert store.is_event_processed("confluence", "evt-2") is False
    store.mark_processed_event("confluence", event_id="evt-2", run_id="run-1", ttl_hours=2)
    assert store.is_event_processed("confluence", "evt-2") is True

    attempts = store.increment_failure_count(
        "confluence", event_id="evt-3", error_message="oops", run_id="run-1"
    )
    assert attempts == 1
    store.mark_terminal_failure(
        "confluence", event_id="evt-3", error_message="fatal", run_id="run-1"
    )

    assert (
        store.get_assessment_snapshot("confluence", target_id="t1", framework_scope="NIST CSF")
        is None
    )
    snapshot = store.upsert_assessment_snapshot(
        "confluence",
        target_id="t1",
        framework_scope="NIST CSF",
        page_version="v2",
        content_hash="xyz",
    )
    assert snapshot.framework_scope == "NIST CSF"
    loaded = store.get_assessment_snapshot("confluence", target_id="t1", framework_scope="NIST CSF")
    assert loaded is not None
    assert loaded.content_hash == "xyz"
