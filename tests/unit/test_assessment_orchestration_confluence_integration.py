"""Integration tests for Confluence MCP with orchestrator, worker, and intake."""

from __future__ import annotations

from unittest.mock import MagicMock

from runtime.assessment_orchestration.intake import (
    build_assessment_job_from_provider_event,
    build_queue_message,
)
from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.mcp.confluence import ConfluenceMCPServer
from runtime.assessment_orchestration.models import (
    AssessedArtifactPackage,
    CorpusGroundingPackage,
    DeliveryOutcome,
)
from runtime.assessment_orchestration.queue import serialise_queue_message
from runtime.assessment_orchestration.worker import process_queue_message_json

# --------------------------------------------------------------------------- #
# Test fixtures                                                                #
# --------------------------------------------------------------------------- #


class FakeAssessmentAgent:
    """Mock assessment agent for orchestration tests."""

    def retrieve_corpus_grounding(
        self, artifact: AssessedArtifactPackage
    ) -> CorpusGroundingPackage:
        return CorpusGroundingPackage(
            corpus_a_results=[{"requirement_id": "SEC-001"}], corpus_b_results=[]
        )

    def generate_assessment(
        self,
        artifact: AssessedArtifactPackage,
        grounding: CorpusGroundingPackage,
        *,
        validation_mode: str = "hard",
    ) -> dict:
        return {
            "schema_version": "v1.1",
            "executive_summary": "Assessment generated for " + artifact.title,
            "findings": [{"finding_id": "F-1", "provider": "confluence"}],
            "citations": ["SEC-001"],
        }


class FakeDeliveryPublisher:
    """Mock delivery publisher for orchestration tests."""

    def post_comment(
        self, target_id: str, *, comment_body: str, identity_mode: str, idempotency_key: str
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("inline",))

    def send_email(
        self, recipients: list[str], *, subject: str, body: str, idempotency_key: str
    ) -> DeliveryOutcome:
        return DeliveryOutcome(success=True, attempted_channels=("email",))


class FakeAuditSink:
    """Mock audit sink for tracking orchestration stages."""

    def __init__(self) -> None:
        self.stages: list[str] = []
        self.payloads: dict = {}

    def record_stage(self, job, stage: str, payload: dict) -> None:
        self.stages.append(stage)
        self.payloads[stage] = payload


# --------------------------------------------------------------------------- #
# Test 1: Orchestrator adapter integration with Confluence                     #
# --------------------------------------------------------------------------- #


def _make_confluence_client_mock() -> ConfluenceMCPServer:
    """Create a mocked ConfluenceMCPServer with injected mock client."""
    mock_client = MagicMock()
    mock_client.get_page.return_value = {
        "id": "1234",
        "title": "Security Policy Review",
        "spaceId": "space-sec",
        "body": {"storage": {"value": "<p>Security policy content here.</p>"}},
        "version": {"number": 5, "authorId": "user-alice", "createdAt": "2026-04-02T10:00:00Z"},
        "ownerId": "user-bob",
        "_links": {"webui": "/wiki/spaces/SEC/pages/1234/Security+Policy"},
    }
    mock_client.get_user.return_value = {
        "accountId": "user-alice",
        "displayName": "Alice Engineer",
        "email": "alice@example.com",
    }
    mock_client.resolve_canonical_url.side_effect = lambda p: (
        f"https://example.atlassian.net{p}" if not p.startswith("http") else p
    )
    return ConfluenceMCPServer(client=mock_client)


def test_orchestrator_adapter_with_confluence_client() -> None:
    """
    Test 1: Orchestrator adapter integration test
    Exercises the full OrchestratorAdapter.run_assessment() flow with ConfluenceMCPServer.
    """
    confluence = _make_confluence_client_mock()
    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=confluence,
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit,
    )

    # Simulate orchestrator accessing Confluence content
    job = build_assessment_job_from_provider_event(
        {
            "event_id": "conf-evt-1",
            "target_id": "1234",
            "target_url": "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Security+Policy",
            "trigger_type": "mention",
        },
        provider_hint="confluence",
        request_identity_mode="app_only",
    )

    result = adapter.run_assessment(job)

    assert result["schema_version"] == "v1.1"
    assert "Security Policy Review" in result["executive_summary"]
    assert result["findings"]
    assert "resolved_target" in audit.stages
    assert "assessment_generated" in audit.stages


def test_confluence_resolve_target_in_orchestrator_context() -> None:
    """Verify Confluence target resolution within orchestrator pattern."""
    confluence = _make_confluence_client_mock()
    target = confluence.resolve_target(
        "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Security+Policy"
    )

    assert target.provider == "confluence"
    assert target.target_id == "1234"
    assert target.target_type == "page"
    assert target.container_id == "SEC"
    # Title is enriched from Confluence API
    assert target.title == "Security Policy Review"


# --------------------------------------------------------------------------- #
# Test 2: Worker end-to-end with Confluence provider event                    #
# --------------------------------------------------------------------------- #


def test_worker_processes_confluence_queue_message() -> None:
    """
    Test 2: Worker end-to-end test
    Exercises the full queue-message-to-assessment-result flow with Confluence provider.
    """
    # Step 1: Build job from Confluence provider event
    job = build_assessment_job_from_provider_event(
        {
            "event_id": "conf-evt-security-1",
            "target_id": "1234",
            "target_url": "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Page+Title",
            "event_type": "mention_notification",
            "trigger_type": "mention",
        },
        provider_hint="confluence",
        request_identity_mode="app_only",
    )

    # Step 2: Build queue message
    message = build_queue_message(job, source_event_id="conf-evt-security-1")

    # Step 3: Process through worker with mocked Confluence client
    confluence = _make_confluence_client_mock()
    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=confluence,
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit,
    )

    result = process_queue_message_json(adapter, serialise_queue_message(message))

    # Assertions
    assert result["schema_version"] == "v1.1"
    assert result["findings"]
    assert "queue_message_received" in audit.stages
    assert "assessment_generated" in audit.stages
    assert "content_retrieved" in audit.stages


def test_worker_routing_confluence_vs_sharepoint() -> None:
    """Verify worker correctly route Confluence vs. SharePoint events through MCP servers."""
    # Confluence event
    confluence_job = build_assessment_job_from_provider_event(
        {
            "event_id": "conf-1",
            "target_id": "1234",
            "target_url": "https://example.atlassian.net/wiki/spaces/SEC/pages/1234",
            "trigger_type": "mention",
        },
        provider_hint="confluence",
    )
    assert confluence_job.provider == "confluence"

    # Even if the target URL looks like SharePoint, provider_hint controls routing
    confluence_job_explicit = build_assessment_job_from_provider_event(
        {
            "event_id": "evt-1",
            "target_id": "doc-99",
            "target_url": "https://tenant.sharepoint.com/sites/x/doc.aspx",
            "trigger_type": "mention",
        },
        provider_hint="confluence",  # Explicitly Confluence
    )
    assert confluence_job_explicit.provider == "confluence"


# --------------------------------------------------------------------------- #
# Test 3: Confluence provider event intake contract mapping                    #
# --------------------------------------------------------------------------- #


def test_build_assessment_job_from_confluence_mention_event() -> None:
    """
    Test 3a: Verify Confluence mention_notification events map to AssessmentJob correctly.
    """
    confluence_event = {
        "event_id": "conf-evt-1",
        "occurred_at": "2026-04-02T00:00:00Z",
        "site_id": "confluence-site",
        "space_key": "SEC",
        "target_id": "1234",
        "target_url": "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Page+Title",
        "event_type": "mention_notification",
        "trigger_type": "mention",
    }

    job = build_assessment_job_from_provider_event(
        confluence_event,
        provider_hint="confluence",
        request_identity_mode="app_only",
    )

    assert job.provider == "confluence"
    assert job.target_id == "1234"
    assert job.target_url == confluence_event["target_url"]
    assert job.trigger_type == "mention"
    assert job.source_type == "provider_event"
    assert job.request_identity_mode == "app_only"
    assert job.job_id  # Generated
    assert job.correlation_id  # Generated


def test_build_assessment_job_from_confluence_update_event() -> None:
    """
    Test 3b: Verify Confluence page_updated events map to AssessmentJob correctly.
    """
    confluence_event = {
        "event_id": "conf-evt-2",
        "occurred_at": "2026-04-02T01:00:00Z",
        "site_id": "confluence-site",
        "space_key": "SEC",
        "target_id": "1234",
        "target_url": "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Page+Title",
        "modified_by": "editor@example.com",
        "event_type": "page_updated",
        "trigger_type": "update",
    }

    job = build_assessment_job_from_provider_event(
        confluence_event,
        provider_hint="confluence",
        request_identity_mode="delegated",
    )

    assert job.provider == "confluence"
    assert job.target_id == "1234"
    assert job.trigger_type == "update"  # From normalised_output in contract
    assert job.request_identity_mode == "delegated"


def test_confluence_event_contract_fields_required() -> None:
    """
    Test 3c: Verify that required fields from Confluence event contract are
    respected during intake.
    """
    # Minimal valid mention_notification (has all required fields)
    minimal_valid_event = {
        "event_id": "conf-evt-1",
        "occurred_at": "2026-04-02T00:00:00Z",
        "site_id": "confluence-site",
        "space_key": "SEC",
        "target_id": "1234",
        "target_url": "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Page+Title",
        "event_type": "mention_notification",
        "trigger_type": "mention",
    }

    job = build_assessment_job_from_provider_event(
        minimal_valid_event,
        provider_hint="confluence",
    )

    assert job.provider == "confluence"
    assert job.target_id == minimal_valid_event["target_id"]
    assert job.target_url == minimal_valid_event["target_url"]


def test_confluence_queue_message_roundtrip() -> None:
    """
    Test 3d: Verify Confluence provider event → job → queue message → worker → result roundtrip.
    """
    # 1. Start with raw Confluence event
    confluence_event = {
        "event_id": "conf-evt-full-flow",
        "occurred_at": "2026-04-02T02:00:00Z",
        "site_id": "confluence-site",
        "space_key": "COMPLIANCE",
        "target_id": "5678",
        "target_url": "https://example.atlassian.net/wiki/spaces/COMPLIANCE/pages/5678/Q2+Review",
        "modified_by": "reviewer@example.com",
        "event_type": "page_updated",
        "trigger_type": "update",
    }

    # 2. Convert to job
    job = build_assessment_job_from_provider_event(confluence_event, provider_hint="confluence")

    # 3. Build queue message
    queue_msg = build_queue_message(job, source_event_id=confluence_event["event_id"])

    # 4. Verify message preserves all essential attributes
    assert queue_msg.job.provider == "confluence"
    assert queue_msg.job.target_id == "5678"
    assert queue_msg.job.trigger_type == "update"
    assert queue_msg.correlation_id == job.correlation_id
    assert queue_msg.message_type == "assessment_requested"

    # 5. Serialise and process through worker
    serialised = serialise_queue_message(queue_msg)
    confluence_mcp = _make_confluence_client_mock()
    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=confluence_mcp,
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit,
    )

    result = process_queue_message_json(adapter, serialised)

    # 6. Verify output is valid assessment result
    assert result["schema_version"] == "v1.1"
    assert "findings" in result
