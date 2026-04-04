"""Integration tests for SharePoint MCP with orchestrator, worker, and intake."""
from __future__ import annotations

from unittest.mock import MagicMock

from runtime.assessment_orchestration.intake import (
    build_assessment_job_from_provider_event,
    build_queue_message,
)
from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.mcp.sharepoint import SharePointMCPServer
from runtime.assessment_orchestration.models import AssessedArtifactPackage, CorpusGroundingPackage, DeliveryOutcome
from runtime.assessment_orchestration.queue import serialise_queue_message
from runtime.assessment_orchestration.worker import process_queue_message_json


# --------------------------------------------------------------------------- #
# Test fixtures                                                                #
# --------------------------------------------------------------------------- #

class FakeAssessmentAgent:
    """Mock assessment agent for orchestration tests."""

    def retrieve_corpus_grounding(self, artifact: AssessedArtifactPackage) -> CorpusGroundingPackage:
        return CorpusGroundingPackage(corpus_a_results=[{"requirement_id": "SEC-001"}], corpus_b_results=[])

    def generate_assessment(
        self, artifact: AssessedArtifactPackage, grounding: CorpusGroundingPackage, *, validation_mode: str = "hard"
    ) -> dict:
        return {
            "schema_version": "v1.1",
            "executive_summary": "Assessment generated for " + artifact.title,
            "findings": [{"finding_id": "F-1", "provider": "sharepoint"}],
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
# Test 1: Orchestrator adapter integration with SharePoint                     #
# --------------------------------------------------------------------------- #

def _make_sharepoint_client_mock() -> SharePointMCPServer:
    """Create a mocked SharePointMCPServer with injected mock client."""
    mock_client = MagicMock()
    mock_client._tenant = "tenant"
    mock_client._site_id = "site-sec"
    mock_client.get_item.return_value = {
        "id": "abc-123",
        "name": "Security Policy Review",
        "webUrl": "https://tenant.sharepoint.com/sites/sec/SitePages/policy.aspx",
        "lastModifiedDateTime": "2026-04-02T10:00:00Z",
        "createdBy": {"user": {"id": "user-alice", "displayName": "Alice Engineer", "mail": "alice@example.com"}},
        "lastModifiedBy": {"user": {"id": "user-alice", "displayName": "Alice Engineer", "mail": "alice@example.com"}},
    }
    mock_client.get_user.return_value = {
        "id": "user-alice",
        "displayName": "Alice Engineer",
        "mail": "alice@example.com",
        "userPrincipalName": "alice@example.onmicrosoft.com",
    }
    mock_client.get_item_content.return_value = "Security policy content here."
    return SharePointMCPServer(client=mock_client)


def test_orchestrator_adapter_with_sharepoint_client() -> None:
    """
    Test 1: Orchestrator adapter integration test
    Exercises the full OrchestratorAdapter.run_assessment() flow with SharePointMCPServer.
    """
    sharepoint = _make_sharepoint_client_mock()
    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=sharepoint,
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit,
    )

    # Simulate orchestrator accessing SharePoint content
    job = build_assessment_job_from_provider_event(
        {
            "event_id": "sp-evt-1",
            "target_id": "abc-123",
            "target_url": "https://tenant.sharepoint.com/sites/sec/SitePages/policy.aspx?id=abc-123",
            "event_type": "mention_notification",
            "trigger_type": "mention",
        },
        provider_hint="sharepoint",
        request_identity_mode="app_only",
    )

    result = adapter.run_assessment(job)

    assert result["schema_version"] == "v1.1"
    assert "Security Policy Review" in result["executive_summary"]
    assert result["findings"]
    assert "resolved_target" in audit.stages
    assert "assessment_generated" in audit.stages


def test_sharepoint_resolve_target_in_orchestrator_context() -> None:
    """Verify SharePoint target resolution within orchestrator pattern."""
    sharepoint = _make_sharepoint_client_mock()
    target = sharepoint.resolve_target("https://tenant.sharepoint.com/sites/sec/SitePages/policy.aspx?id=abc-123")

    assert target.provider == "sharepoint"
    assert target.target_id == "abc-123"
    assert target.target_type == "page"
    assert target.container_id == "sec"


# --------------------------------------------------------------------------- #
# Test 2: Worker end-to-end with SharePoint provider event                    #
# --------------------------------------------------------------------------- #

def test_worker_processes_sharepoint_queue_message() -> None:
    """
    Test 2: Worker end-to-end test
    Exercises the full queue-message-to-assessment-result flow with SharePoint provider.
    """
    # Step 1: Build job from SharePoint provider event
    job = build_assessment_job_from_provider_event(
        {
            "event_id": "sp-evt-security-1",
            "target_id": "abc-123",
            "target_url": "https://tenant.sharepoint.com/sites/sec/SitePages/policy.aspx?id=abc-123",
            "event_type": "mention_notification",
            "trigger_type": "mention",
        },
        provider_hint="sharepoint",
        request_identity_mode="app_only",
    )

    # Step 2: Build queue message
    message = build_queue_message(job, source_event_id="sp-evt-security-1")

    # Step 3: Process through worker with mocked SharePoint client
    sharepoint = _make_sharepoint_client_mock()
    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=sharepoint,
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


def test_worker_routing_sharepoint_events() -> None:
    """Verify worker correctly routes SharePoint events through MCP server."""
    sp_job = build_assessment_job_from_provider_event(
        {
            "event_id": "sp-1",
            "target_id": "abc-123",
            "target_url": "https://tenant.sharepoint.com/sites/sec/item.aspx",
            "event_type": "mention_notification",
            "trigger_type": "mention",
        },
        provider_hint="sharepoint",
    )
    assert sp_job.provider == "sharepoint"


# --------------------------------------------------------------------------- #
# Test 3: SharePoint provider event intake contract mapping                    #
# --------------------------------------------------------------------------- #

def test_build_assessment_job_from_sharepoint_mention_event() -> None:
    """
    Test 3a: Verify SharePoint mention_notification events map to AssessmentJob correctly.
    """
    sharepoint_event = {
        "event_id": "sp-evt-1",
        "occurred_at": "2026-04-02T00:00:00Z",
        "tenant_id": "tenant-1",
        "site_id": "site-sec",
        "target_id": "abc-123",
        "target_url": "https://tenant.sharepoint.com/sites/sec/SitePages/policy.aspx?id=abc-123",
        "event_type": "mention_notification",
        "trigger_type": "mention",
    }

    job = build_assessment_job_from_provider_event(
        sharepoint_event,
        provider_hint="sharepoint",
        request_identity_mode="app_only",
    )

    assert job.provider == "sharepoint"
    assert job.target_id == "abc-123"
    assert job.target_url == sharepoint_event["target_url"]
    assert job.trigger_type == "mention"
    assert job.source_type == "provider_event"
    assert job.request_identity_mode == "app_only"


def test_build_assessment_job_from_sharepoint_update_event() -> None:
    """
    Test 3b: Verify SharePoint item_updated events map to AssessmentJob correctly.
    """
    sharepoint_event = {
        "event_id": "sp-evt-2",
        "occurred_at": "2026-04-02T01:00:00Z",
        "tenant_id": "tenant-1",
        "site_id": "site-sec",
        "target_id": "abc-123",
        "target_url": "https://tenant.sharepoint.com/sites/sec/SitePages/policy.aspx?id=abc-123",
        "modified_by": "editor@example.com",
        "event_type": "item_updated",
        "trigger_type": "update",
    }

    job = build_assessment_job_from_provider_event(
        sharepoint_event,
        provider_hint="sharepoint",
        request_identity_mode="delegated",
    )

    assert job.provider == "sharepoint"
    assert job.target_id == "abc-123"
    assert job.trigger_type == "update"
    assert job.request_identity_mode == "delegated"


def test_sharepoint_event_contract_fields_required() -> None:
    """
    Test 3c: Verify that required fields from SharePoint event contract are
    respected during intake.
    """
    minimal_valid_event = {
        "event_id": "sp-evt-1",
        "occurred_at": "2026-04-02T00:00:00Z",
        "tenant_id": "tenant-1",
        "site_id": "site-sec",
        "target_id": "abc-123",
        "target_url": "https://tenant.sharepoint.com/sites/sec/SitePages/page.aspx?id=abc-123",
        "event_type": "mention_notification",
        "trigger_type": "mention",
    }

    job = build_assessment_job_from_provider_event(
        minimal_valid_event,
        provider_hint="sharepoint",
    )

    assert job.provider == "sharepoint"
    assert job.target_id == minimal_valid_event["target_id"]
    assert job.target_url == minimal_valid_event["target_url"]


def test_sharepoint_queue_message_roundtrip() -> None:
    """
    Test 3d: Verify SharePoint provider event → job → queue message → worker → result roundtrip.
    """
    # 1. Start with raw SharePoint event
    sharepoint_event = {
        "event_id": "sp-evt-full-flow",
        "occurred_at": "2026-04-02T02:00:00Z",
        "tenant_id": "tenant-1",
        "site_id": "site-compliance",
        "target_id": "xyz-789",
        "target_url": "https://tenant.sharepoint.com/sites/compliance/Documents/Q2%20Review.docx?id=xyz-789",
        "modified_by": "reviewer@example.com",
        "event_type": "item_updated",
        "trigger_type": "update",
    }

    # 2. Convert to job
    job = build_assessment_job_from_provider_event(sharepoint_event, provider_hint="sharepoint")

    # 3. Build queue message
    queue_msg = build_queue_message(job, source_event_id=sharepoint_event["event_id"])

    # 4. Verify message preserves all essential attributes
    assert queue_msg.job.provider == "sharepoint"
    assert queue_msg.job.target_id == "xyz-789"
    assert queue_msg.job.trigger_type == "update"
    assert queue_msg.correlation_id == job.correlation_id
    assert queue_msg.message_type == "assessment_requested"

    # 5. Serialise and process through worker
    serialised = serialise_queue_message(queue_msg)
    sharepoint_mcp = _make_sharepoint_client_mock()
    audit = FakeAuditSink()
    adapter = OrchestratorAdapter(
        content_client=sharepoint_mcp,
        assessment_agent=FakeAssessmentAgent(),
        delivery_publisher=FakeDeliveryPublisher(),
        audit_sink=audit,
    )

    result = process_queue_message_json(adapter, serialised)

    # 6. Verify output is valid assessment result
    assert result["schema_version"] == "v1.1"
    assert "findings" in result
