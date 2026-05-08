from __future__ import annotations

from runtime.assessment_orchestration.dynamo_state_store import DynamoDBPollingStateStore
from runtime.assessment_orchestration.polling_worker import (
    PollerConfig,
    create_cosmos_state_store_from_env,
    run_poll_cycle,
)
from runtime.assessment_orchestration.state_store import InMemoryPollingStateStore


class _FakeServer:
    def __init__(self, mentions: list[dict]) -> None:
        self._mentions = mentions

    def get_recent_mentions(self, *, since: str = "", scope_filter: dict | None = None) -> dict:
        return {"mentions": list(self._mentions)}


class _FakeAdapter:
    pass


class _FakeDynamoSession:
    def resource(self, service_name: str):
        assert service_name == "dynamodb"
        return self

    def Table(self, table_name: str):
        return {"table_name": table_name}


def test_aws_env_uses_dynamodb_state_store_scaffold() -> None:
    store = create_cosmos_state_store_from_env(
        {
            "CLOUD_PROVIDER": "aws",
            "DYNAMODB_TABLE": "orchestration-state",
            "AWS_REGION": "ap-southeast-2",
        },
        aws_session=_FakeDynamoSession(),
    )

    assert isinstance(store, DynamoDBPollingStateStore)


def test_restart_mid_backlog_continues_from_first_unprocessed_event_scaffold() -> None:
    """Scaffold integration: first cycle fails mid-backlog, second cycle resumes.

    This test integrates polling worker cycle logic with state persistence behaviour
    using in-memory state store as a deterministic test harness.
    """
    mentions = [
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "A",
            "target_id": "1",
            "target_url": "https://example/1",
        },
        {
            "event_id": "e-2",
            "occurred_at": "2026-04-04T10:01:00+00:00",
            "title": "B",
            "target_id": "2",
            "target_url": "https://example/2",
        },
        {
            "event_id": "e-3",
            "occurred_at": "2026-04-04T10:02:00+00:00",
            "title": "C",
            "target_id": "3",
            "target_url": "https://example/3",
        },
    ]
    server = _FakeServer(mentions)
    state_store = InMemoryPollingStateStore()
    handled: list[str] = []

    def _first_cycle_handler(event: dict) -> None:
        event_id = str(event.get("event_id") or "")
        handled.append(event_id)
        if event_id == "e-2":
            raise RuntimeError("forced failure on second event")

    # First cycle: e-1 succeeds, e-2 fails (retryable), cycle stops before e-3.
    first = run_poll_cycle(
        config=PollerConfig(max_event_attempts=2),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=_first_cycle_handler,
    )

    assert first.processed_events == 1
    assert state_store.load_state("confluence").watermark == "2026-04-04T10:00:00+00:00"

    def _second_cycle_handler(event: dict) -> None:
        handled.append(str(event.get("event_id") or ""))

    # Second cycle: resumes and processes remaining backlog.
    second = run_poll_cycle(
        config=PollerConfig(max_event_attempts=2),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=_second_cycle_handler,
    )

    assert second.processed_events == 2
    assert state_store.load_state("confluence").watermark == "2026-04-04T10:02:00+00:00"
    assert handled == ["e-1", "e-2", "e-2", "e-3"]


def test_same_timestamp_ordering_is_deterministic_scaffold() -> None:
    """Scaffold integration: same-timestamp events sort by title then event_id."""
    mentions = [
        {
            "event_id": "e-3",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "Zulu",
            "target_id": "3",
            "target_url": "https://example/3",
        },
        {
            "event_id": "e-2",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "Alpha",
            "target_id": "2",
            "target_url": "https://example/2",
        },
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "Alpha",
            "target_id": "1",
            "target_url": "https://example/1",
        },
    ]
    server = _FakeServer(mentions)
    state_store = InMemoryPollingStateStore()
    handled: list[str] = []

    run_poll_cycle(
        config=PollerConfig(),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=lambda event: handled.append(str(event.get("event_id") or "")),
    )

    assert handled == ["e-1", "e-2", "e-3"]
