from __future__ import annotations

from datetime import UTC, datetime, timedelta

from runtime.assessment_orchestration.polling_worker import (
    PollerConfig,
    _build_recent_mentions_query,
    _process_assessment_event,
    _requested_frameworks_for_event,
    _requested_frameworks_from_text,
    run_poll_cycle,
)
from runtime.assessment_orchestration.state_store import InMemoryPollingStateStore


class _FakeServer:
    def __init__(self, mentions: list[dict]) -> None:
        self._mentions = mentions
        self.last_since = ""
        self.last_scope_filter: dict | None = None
        self.page_version = 1
        self.page_content = "test content"
        self.discussion_comments: list[str] = []
        self.discussion_author_id = "acct-1"
        self.discussion_entries: list[dict] = []

    def get_recent_mentions(self, *, since: str = "", scope_filter: dict | None = None) -> dict:
        self.last_since = since
        self.last_scope_filter = scope_filter
        return {"mentions": list(self._mentions)}

    def get_content_by_id(
        self,
        target_id: str,
        *,
        identity_mode: str,
        include_discussion_context: bool = False,
    ):
        class _Artifact:
            def __init__(self, content: str, version: int, discussion: list[dict]) -> None:
                self.content = content
                self.title = "Test Page"
                self.canonical_url = f"https://example/{target_id}"
                self.metadata = {"version": version, "space_key": "SEC"}
                self.discussion_context = discussion

        discussion = []
        if include_discussion_context:
            if self.discussion_entries:
                discussion = [dict(item) for item in self.discussion_entries]
            else:
                discussion = [
                    {"comment_id": f"comment-{idx + 1}", "text": text, "author_id": self.discussion_author_id}
                    for idx, text in enumerate(self.discussion_comments)
                ]
        return _Artifact(content=self.page_content, version=self.page_version, discussion=discussion)


class _FakeAdapter:
    def __init__(self) -> None:
        self.jobs: list = []

    def run_assessment(self, job):
        self.jobs.append(job)
        scope = str(job.metadata.get("requested_framework") or "Essential Eight")
        return {
            "executive_summary": f"Assessment for {scope}",
            "findings": [],
            "overall_risk_rating": "low",
            "metadata": {"framework_scope": scope},
        }


class _PostingServer(_FakeServer):
    def __init__(self, mentions: list[dict]) -> None:
        super().__init__(mentions)
        self.posts: list[dict] = []

    def post_comment(
        self, target_id: str, *, comment_body: str, identity_mode: str, idempotency_key: str
    ):
        self.posts.append(
            {
                "target_id": target_id,
                "comment_body": comment_body,
                "identity_mode": identity_mode,
                "idempotency_key": idempotency_key,
            }
        )

        class _Outcome:
            success = True
            failures: tuple[str, ...] = ()

        return _Outcome()


class _LeaseRejectedStateStore(InMemoryPollingStateStore):
    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        return False


def test_run_poll_cycle_orders_by_occurred_at_then_title_then_event_id() -> None:
    mentions = [
        {
            "event_id": "e-3",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "zeta",
            "target_id": "1",
            "target_url": "https://x/1",
        },
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T09:00:00+00:00",
            "title": "beta",
            "target_id": "2",
            "target_url": "https://x/2",
        },
        {
            "event_id": "e-2",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "alpha",
            "target_id": "3",
            "target_url": "https://x/3",
        },
        {
            "event_id": "e-0",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "alpha",
            "target_id": "4",
            "target_url": "https://x/4",
        },
    ]
    server = _FakeServer(mentions)
    state_store = InMemoryPollingStateStore()
    seen: list[str] = []

    def _handler(event: dict) -> None:
        seen.append(str(event.get("event_id") or ""))

    result = run_poll_cycle(
        config=PollerConfig(),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=_handler,
    )

    assert result.acquired_lease is True
    assert seen == ["e-1", "e-0", "e-2", "e-3"]


def test_run_poll_cycle_advances_watermark_after_each_event() -> None:
    mentions = [
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "a",
            "target_id": "1",
            "target_url": "https://x/1",
        },
        {
            "event_id": "e-2",
            "occurred_at": "2026-04-04T10:01:00+00:00",
            "title": "b",
            "target_id": "2",
            "target_url": "https://x/2",
        },
    ]
    server = _FakeServer(mentions)
    state_store = InMemoryPollingStateStore()
    committed: list[str] = []

    def _handler(event: dict) -> None:
        # Observe watermark after first event has been committed.
        if event.get("event_id") == "e-2":
            committed.append(state_store.load_state("confluence").watermark)

    result = run_poll_cycle(
        config=PollerConfig(),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=_handler,
    )

    assert result.processed_events == 2
    assert committed == ["2026-04-04T10:00:00+00:00"]
    assert state_store.load_state("confluence").watermark == "2026-04-04T10:01:00+00:00"


def test_run_poll_cycle_terminal_failure_does_not_block_next_event() -> None:
    mentions = [
        {
            "event_id": "bad",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "a",
            "target_id": "1",
            "target_url": "https://x/1",
        },
        {
            "event_id": "good",
            "occurred_at": "2026-04-04T10:01:00+00:00",
            "title": "b",
            "target_id": "2",
            "target_url": "https://x/2",
        },
    ]
    server = _FakeServer(mentions)
    state_store = InMemoryPollingStateStore()
    seen: list[str] = []

    def _handler(event: dict) -> None:
        event_id = str(event.get("event_id") or "")
        seen.append(event_id)
        if event_id == "bad":
            raise RuntimeError("forced failure")

    result = run_poll_cycle(
        config=PollerConfig(max_event_attempts=1),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=_handler,
    )

    assert seen == ["bad", "good"]
    assert result.terminal_failures == 1
    assert result.processed_events == 1
    assert state_store.load_state("confluence").watermark == "2026-04-04T10:01:00+00:00"


def test_run_poll_cycle_returns_early_when_lease_not_acquired() -> None:
    mentions = [
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "a",
            "target_id": "1",
            "target_url": "https://x/1",
            "trigger_text": "@compliance-agent Review against NIST CSF",
            "mentioner_account_id": "acct-1",
        }
    ]
    server = _FakeServer(mentions)
    state_store = _LeaseRejectedStateStore()
    seen: list[str] = []

    result = run_poll_cycle(
        config=PollerConfig(),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=lambda event: seen.append(str(event.get("event_id") or "")),
    )

    assert result.acquired_lease is False
    assert result.fetched_events == 0
    assert result.processed_events == 0
    assert seen == []


def test_run_poll_cycle_retryable_failure_stops_cycle_without_advancing_watermark() -> None:
    mentions = [
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "a",
            "target_id": "1",
            "target_url": "https://x/1",
        },
        {
            "event_id": "e-2",
            "occurred_at": "2026-04-04T10:01:00+00:00",
            "title": "b",
            "target_id": "2",
            "target_url": "https://x/2",
        },
    ]
    server = _FakeServer(mentions)
    state_store = InMemoryPollingStateStore()
    seen: list[str] = []

    def _handler(event: dict) -> None:
        event_id = str(event.get("event_id") or "")
        seen.append(event_id)
        raise RuntimeError("retryable")

    result = run_poll_cycle(
        config=PollerConfig(max_event_attempts=2),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
        process_event=_handler,
    )

    assert result.acquired_lease is True
    assert result.fetched_events == 2
    assert result.processed_events == 0
    assert result.terminal_failures == 0
    assert seen == ["e-1"]
    assert state_store.load_state("confluence").watermark == ""


def test_build_recent_mentions_query_applies_space_scope_and_window_bound() -> None:
    now = datetime.now(UTC)
    in_window = (now - timedelta(minutes=1)).isoformat()
    out_of_window = (now + timedelta(minutes=6)).isoformat()
    server = _FakeServer(
        [
            {
                "event_id": "e-future",
                "occurred_at": out_of_window,
                "title": "later",
                "target_id": "2",
                "target_url": "https://x/2",
            },
            {
                "event_id": "e-now",
                "occurred_at": in_window,
                "title": "now",
                "target_id": "1",
                "target_url": "https://x/1",
            },
        ]
    )

    result = _build_recent_mentions_query(
        server=server,  # type: ignore[arg-type]
        since_iso=(now - timedelta(hours=1)).isoformat(),
        window_end=now,
        space_keys=("DOC", "OPS"),
    )

    assert [item["event_id"] for item in result] == ["e-now"]
    assert server.last_scope_filter == {"space_keys": ["DOC", "OPS"]}


def test_requested_frameworks_from_text_detects_specific_frameworks() -> None:
    text = "@assessment-agent please review Essential Eight and AESCSF for this page"
    result = _requested_frameworks_from_text(text)
    assert result == ("Essential Eight", "AESCSF")


def test_requested_frameworks_from_text_requires_explicit_all_intent() -> None:
    generic = "I updated all references in this sentence"
    explicit = "Please assess this page against all frameworks"

    assert _requested_frameworks_from_text(generic) == ()
    assert _requested_frameworks_from_text(explicit) == (
        "Essential Eight",
        "ISM",
        "AESCSF",
        "NIST CSF",
        "PSPF",
        "PCI DSS",
        "CIS Controls",
    )


def test_requested_frameworks_for_event_uses_trigger_text() -> None:
    event = {"trigger_text": "Review against ISM then NIST CSF", "title": "ignored title"}
    assert _requested_frameworks_for_event(event) == ("ISM", "NIST CSF")


def test_requested_frameworks_for_event_trigger_text_takes_precedence_over_title() -> None:
    event = {
        "trigger_text": "@compliance-agent Review against CIS controls",
        "title": "Assess this page against all frameworks",
    }
    assert _requested_frameworks_for_event(event) == ("CIS Controls",)


def test_requested_frameworks_from_text_maps_generic_cyber_security_framework_to_nist() -> None:
    text = "Please review this page against the cyber security framework."
    assert _requested_frameworks_from_text(text) == ("NIST CSF",)


def test_requested_frameworks_from_text_maps_full_australian_energy_sector_phrase_to_aescsf_only() -> (
    None
):
    text = "Assess this page against the full Australian Energy Sector Cyber Security Framework."
    assert _requested_frameworks_from_text(text) == ("AESCSF",)


def test_requested_frameworks_from_text_supports_essential_8_variant() -> None:
    text = "Please perform an Essential 8 review."
    assert _requested_frameworks_from_text(text) == ("Essential Eight",)


def test_requested_frameworks_from_text_detects_pspf_pci_and_cis() -> None:
    text = "Please assess against PSPF, PCI DSS and CIS Controls."
    assert _requested_frameworks_from_text(text) == ("PSPF", "PCI DSS", "CIS Controls")


def test_process_assessment_event_posts_one_comment_per_requested_framework() -> None:
    server = _PostingServer([])
    adapter = _FakeAdapter()
    event = {
        "event_id": "e-123",
        "target_id": "123",
        "target_url": "https://example/123",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "Please review Essential Eight and AESCSF.",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=InMemoryPollingStateStore(),
        source="confluence",
        event=event,
        dry_run=False,
    )

    assert [job.metadata.get("requested_framework") for job in adapter.jobs] == [
        "Essential Eight",
        "AESCSF",
    ]
    assert len(server.posts) == 2
    assert server.posts[0]["idempotency_key"].endswith("essential-eight")
    assert server.posts[1]["idempotency_key"].endswith("aescsf")
    assert "<strong>Page version:</strong> 1" in server.posts[0]["comment_body"]
    assert "<strong>Page version:</strong> 1" in server.posts[1]["comment_body"]


def test_process_assessment_event_posts_clarification_when_framework_is_unspecified() -> None:
    server = _PostingServer([])
    adapter = _FakeAdapter()
    state_store = InMemoryPollingStateStore()
    event = {
        "event_id": "e-default",
        "target_id": "987",
        "target_url": "https://example/987",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "Please review this page.",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=state_store,
        source="confluence",
        event=event,
        dry_run=False,
    )

    assert len(adapter.jobs) == 0
    assert len(server.posts) == 1
    assert server.posts[0]["idempotency_key"].endswith("clarify-framework")
    assert "did not clearly specify a supported framework" in server.posts[0]["comment_body"]
    assert "@compliance-agent NIST CSF" in server.posts[0]["comment_body"]
    recent = state_store.list_recent_page_assessments(
        "confluence",
        since_iso="2000-01-01T00:00:00+00:00",
        limit=10,
    )
    assert len(recent) == 1
    assert recent[0].status == "clarification_required"
    assert recent[0].framework_scope == "Clarification Required"


def test_process_assessment_event_uses_discussion_context_when_trigger_excerpt_is_ambiguous() -> None:
    server = _PostingServer([])
    server.discussion_entries = [
        {
            "comment_id": "comment-123",
            "text": "@compliance-agent Review against NIST framework",
            "author_id": "acct-1",
        }
    ]
    adapter = _FakeAdapter()
    event = {
        "event_id": "e-discussion",
        "content_id": "comment-123",
        "target_id": "321",
        "target_url": "https://example/321",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "@compliance-agent",
        "title": "General review",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=InMemoryPollingStateStore(),
        source="confluence",
        event=event,
        dry_run=False,
    )

    assert len(adapter.jobs) == 1
    assert adapter.jobs[0].metadata.get("requested_framework") == "NIST CSF"
    assert len(server.posts) == 1
    assert server.posts[0]["idempotency_key"].endswith("nist-csf")


def test_process_assessment_event_prefers_triggering_comment_over_other_history() -> None:
    server = _PostingServer([])
    server.discussion_entries = [
        {
            "comment_id": "older-comment",
            "text": "@compliance-agent Assess this page against all frameworks",
            "author_id": "acct-1",
        },
        {
            "comment_id": "trigger-comment",
            "text": "@compliance-agent Review against CIS Controls",
            "author_id": "acct-1",
        },
    ]
    adapter = _FakeAdapter()
    event = {
        "event_id": "e-triggering-comment-precedence",
        "content_id": "trigger-comment",
        "target_id": "401",
        "target_url": "https://example/401",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "@compliance-agent",
        "title": "Review",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=InMemoryPollingStateStore(),
        source="confluence",
        event=event,
        dry_run=False,
    )

    assert len(adapter.jobs) == 1
    assert adapter.jobs[0].metadata.get("requested_framework") == "CIS Controls"
    assert len(server.posts) == 1
    assert server.posts[0]["idempotency_key"].endswith("cis-controls")


def test_process_assessment_event_falls_back_to_history_when_trigger_comment_missing() -> None:
    server = _PostingServer([])
    server.discussion_entries = [
        {
            "comment_id": "older-comment",
            "text": "@compliance-agent Assess this page against all frameworks",
            "author_id": "acct-1",
        }
    ]
    adapter = _FakeAdapter()
    event = {
        "event_id": "e-trigger-comment-missing",
        "content_id": "missing-comment-id",
        "target_id": "402",
        "target_url": "https://example/402",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "@compliance-agent",
        "title": "Review",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=InMemoryPollingStateStore(),
        source="confluence",
        event=event,
        dry_run=False,
    )

    assert [job.metadata.get("requested_framework") for job in adapter.jobs] == [
        "Essential Eight",
        "ISM",
        "AESCSF",
        "NIST CSF",
        "PSPF",
        "PCI DSS",
        "CIS Controls",
    ]
    assert len(server.posts) == 7
    assert server.posts[0]["idempotency_key"].endswith("essential-eight")
    assert server.posts[-1]["idempotency_key"].endswith("cis-controls")


def test_process_assessment_event_discussion_fallback_ignores_other_authors() -> None:
    server = _PostingServer([])
    server.discussion_comments = ["Review against Essential Eight and AESCSF"]
    server.discussion_author_id = "acct-other"
    adapter = _FakeAdapter()
    event = {
        "event_id": "e-discussion-author-filter",
        "target_id": "322",
        "target_url": "https://example/322",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "@compliance-agent",
        "title": "General review",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=InMemoryPollingStateStore(),
        source="confluence",
        event=event,
        dry_run=False,
    )

    assert len(adapter.jobs) == 0
    assert len(server.posts) == 1
    assert server.posts[0]["idempotency_key"].endswith("clarify-framework")


def test_process_assessment_event_skips_reassessment_when_page_unchanged() -> None:
    server = _PostingServer([])
    adapter = _FakeAdapter()
    state_store = InMemoryPollingStateStore()
    event = {
        "event_id": "e-unchanged",
        "target_id": "555",
        "target_url": "https://example/555",
        "trigger_type": "mention",
        "mentioner_account_id": "acct-1",
        "trigger_text": "Please review Essential Eight.",
    }

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=state_store,
        source="confluence",
        event=event,
        dry_run=False,
    )
    assert len(adapter.jobs) == 1
    assert len(server.posts) == 1

    _process_assessment_event(
        adapter=adapter,  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        state_store=state_store,
        source="confluence",
        event={**event, "event_id": "e-unchanged-2"},
        dry_run=False,
    )

    assert len(adapter.jobs) == 1
    assert len(server.posts) == 2
    assert "No changes were detected" in server.posts[1]["comment_body"]
    assert "<strong>Page version:</strong> 1" in server.posts[1]["comment_body"]
    assert server.posts[1]["idempotency_key"].endswith("essential-eight-nochange")

    recent = state_store.list_recent_page_assessments(
        "confluence",
        since_iso="2000-01-01T00:00:00+00:00",
        limit=10,
    )
    assert len(recent) == 1
    assert recent[0].status == "no_change"
    assert recent[0].title == "Test Page"


def test_run_poll_cycle_persists_latest_poll_summary() -> None:
    mentions = [
        {
            "event_id": "e-1",
            "occurred_at": "2026-04-04T10:00:00+00:00",
            "title": "a",
            "target_id": "1",
            "target_url": "https://x/1",
            "trigger_text": "@compliance-agent Review against NIST CSF",
            "mentioner_account_id": "acct-1",
        }
    ]
    server = _PostingServer(mentions)
    state_store = InMemoryPollingStateStore()

    result = run_poll_cycle(
        config=PollerConfig(space_keys=("SEC",)),
        state_store=state_store,
        server=server,  # type: ignore[arg-type]
        adapter=_FakeAdapter(),  # type: ignore[arg-type]
    )

    assert result.processed_events == 1
    summary = state_store.get_latest_poll_run_summary("confluence")
    assert summary is not None
    assert summary.mentions_found == 1
    assert summary.jobs_queued == 1
    assert summary.space_keys == ("SEC",)
    assessments = state_store.list_recent_page_assessments(
        "confluence",
        since_iso="2000-01-01T00:00:00+00:00",
        limit=10,
    )
    assert len(assessments) == 1
    assert assessments[0].status == "assessed"
    assert assessments[0].overall_risk == "low"
