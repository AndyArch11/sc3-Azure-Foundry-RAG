from __future__ import annotations

from runtime.assessment_orchestration.interfaces import OrchestratorAdapter
from runtime.assessment_orchestration.mcp.confluence import ConfluenceMCPServer
from runtime.assessment_orchestration.runtime_wiring import (
    create_confluence_mcp_server_from_env,
    create_orchestrator_adapter_from_env,
)


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
