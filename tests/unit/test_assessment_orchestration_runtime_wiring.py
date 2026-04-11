from __future__ import annotations

import types

import pytest

from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.mcp.confluence import ConfluenceMCPServer
from runtime.assessment_orchestration.runtime_wiring import (DefaultAssessmentAgent,
                                                             DefaultDeliveryPublisher,
                                                             StdoutAuditSink, _required,
                                                             _resolve_cloud_id,
                                                             create_confluence_mcp_server_from_env,
                                                             create_orchestrator_adapter_from_env)


def test_create_confluence_mcp_server_from_env_basic_mode() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_API_TOKEN": "token-1",
        "CONFLUENCE_AUTH_MODE": "basic",
        "CONFLUENCE_AUTH_EMAIL": "bot@example.com",
    }

    server = create_confluence_mcp_server_from_env(env)

    assert isinstance(server, ConfluenceMCPServer)
    assert server._client is not None
    assert server._client._auth_mode == "basic"


def test_create_confluence_mcp_server_from_env_bearer_mode() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_API_TOKEN": "token-1",
        "CONFLUENCE_AUTH_MODE": "bearer",
        "CONFLUENCE_CLOUD_ID": "cloud-123",
    }

    server = create_confluence_mcp_server_from_env(env)

    assert isinstance(server, ConfluenceMCPServer)
    assert server._client is not None
    assert server._client._auth_mode == "bearer"


def test_create_confluence_mcp_server_from_env_oauth_mode() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_AUTH_MODE": "oauth",
        "CONFLUENCE_CLOUD_ID": "cloud-123",
        "CONFLUENCE_OAUTH_CLIENT_ID": "client-id-1",
        "CONFLUENCE_OAUTH_CLIENT_SECRET": "client-secret-1",
    }

    server = create_confluence_mcp_server_from_env(env)

    assert isinstance(server, ConfluenceMCPServer)
    assert server._client is not None
    assert server._client._auth_mode == "oauth"


def test_create_confluence_mcp_server_from_env_oauth_mode_with_access_token() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_AUTH_MODE": "oauth",
        "CONFLUENCE_CLOUD_ID": "cloud-123",
        "CONFLUENCE_OAUTH_ACCESS_TOKEN": "oauth-token-1",
    }

    server = create_confluence_mcp_server_from_env(env)

    assert isinstance(server, ConfluenceMCPServer)
    assert server._client is not None
    assert server._client._auth_mode == "oauth"


def test_create_orchestrator_adapter_from_env() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_API_TOKEN": "token-1",
        "CONFLUENCE_AUTH_MODE": "basic",
        "CONFLUENCE_AUTH_EMAIL": "bot@example.com",
    }

    adapter = create_orchestrator_adapter_from_env(env)

    assert isinstance(adapter, OrchestratorAdapter)


def test_runtime_wiring_default_helpers_and_required(capsys: pytest.CaptureFixture[str]) -> None:
    agent = DefaultAssessmentAgent()
    artifact = types.SimpleNamespace(provider="confluence", target_id="t1", title="T1")
    grounding = agent.retrieve_corpus_grounding(artifact)
    assessment = agent.generate_assessment(artifact, grounding)
    assert assessment["schema_version"] == "v1.1"

    per_control = agent.generate_per_control_assessment(artifact, grounding)
    assert per_control["schema_version"] == "v1.1"

    publisher = DefaultDeliveryPublisher()
    assert publisher.post_comment(
        "t1", comment_body="x", identity_mode="app_only", idempotency_key="k"
    ).success
    assert publisher.send_email(
        ["a@example.com"], subject="s", body="b", idempotency_key="k"
    ).success

    sink = StdoutAuditSink()
    sink.record_stage(types.SimpleNamespace(job_id="j1", correlation_id="c1"), "stage", {"x": 1})
    out = capsys.readouterr().out
    assert "assessment_stage" in out

    assert _required({"K": " v "}, "K") == "v"
    with pytest.raises(ValueError, match="Missing required environment variable"):
        _required({}, "MISSING")


def test_resolve_cloud_id_and_invalid_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"cloudId": "cloud-123"}

    monkeypatch.setattr(
        "runtime.assessment_orchestration.runtime_wiring.requests.get",
        lambda *args, **kwargs: _Resp(),
    )
    assert _resolve_cloud_id("https://example.atlassian.net") == "cloud-123"

    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_API_TOKEN": "token-1",
        "CONFLUENCE_AUTH_MODE": "invalid",
    }
    with pytest.raises(ValueError, match="CONFLUENCE_AUTH_MODE"):
        create_confluence_mcp_server_from_env(env)


def test_resolve_cloud_id_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {}

    monkeypatch.setattr(
        "runtime.assessment_orchestration.runtime_wiring.requests.get",
        lambda *args, **kwargs: _Resp(),
    )
    with pytest.raises(ValueError, match="Could not resolve cloudId"):
        _resolve_cloud_id("https://example.atlassian.net")


def test_create_confluence_oauth_requires_credentials() -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_AUTH_MODE": "oauth",
        "CONFLUENCE_CLOUD_ID": "cloud-123",
    }
    with pytest.raises(ValueError, match="oauth mode requires"):
        create_confluence_mcp_server_from_env(env)


def test_create_orchestrator_adapter_falls_back_to_default_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    env = {
        "CONFLUENCE_BASE_URL": "https://example.atlassian.net",
        "CONFLUENCE_API_TOKEN": "token-1",
        "CONFLUENCE_AUTH_MODE": "basic",
        "CONFLUENCE_AUTH_EMAIL": "bot@example.com",
        "ASSESSMENT_SKILLS_ROOT": str(tmp_path / "missing-skills"),
    }

    def _boom(_values):
        raise ValueError("missing env")

    monkeypatch.setattr(
        "runtime.assessment_orchestration.runtime_wiring.create_search_backed_assessment_agent_from_env",
        _boom,
    )

    adapter = create_orchestrator_adapter_from_env(env)
    assert isinstance(adapter, OrchestratorAdapter)
