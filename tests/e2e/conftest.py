"""Shared fixtures for Playwright-based UI tests.

These tests exercise the rendered Query Console UI (Jinja2 templates +
vanilla JS) against a running query-web instance. They require the local
Docker Compose stack (or an equivalent reachable instance) to already be up —
see README.md "Local Development" for how to start it.

Environment variables
----------------------
QUERY_WEB_BASE_URL    Explicit base URL; otherwise localhost is tried before the Docker host
QUERY_WEB_HOST_PORT   Host port used by the Docker-host fallback (default: 8080)
QUERY_WEB_AUTH_TOKEN  Optional auth token to submit with forms (default: "")
"""

from __future__ import annotations

import os

import pytest
import requests

_DEFAULT_BASE_URL = "http://localhost:8080"
_DOCKER_HOSTNAME = "host.docker.internal"


def _healthcheck(base_url: str) -> bool:
    """Return whether a query-web health endpoint responds successfully."""
    try:
        response = requests.get(f"{base_url}/health", timeout=2)
        response.raise_for_status()
        return True
    except requests.RequestException:
        return False


def _resolve_base_url() -> str:
    """Resolve the E2E target, preferring explicit config and local loopback."""
    configured = os.getenv("QUERY_WEB_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured

    localhost_url = _DEFAULT_BASE_URL
    host_port = os.getenv("QUERY_WEB_HOST_PORT", "8080").strip() or "8080"
    docker_host_url = f"http://{_DOCKER_HOSTNAME}:{host_port}"
    for candidate in (localhost_url, docker_host_url):
        if _healthcheck(candidate):
            return candidate

    # Keep the default in the failure case so the autouse fixture emits a
    # familiar skip message rather than hiding the primary local URL.
    return localhost_url


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the query-web instance under test.

    Overrides the pytest-playwright default (which otherwise requires
    --base-url on the command line) so tests work out of the box against the
    local Docker Compose stack.

    Returns:
        The base URL, stripped of any trailing slash.
    """
    return _resolve_base_url()


@pytest.fixture(scope="session")
def auth_token() -> str:
    """Optional auth token configured for the target query-web instance.

    Returns:
        The auth token, or an empty string when auth is disabled (the local
        stack's default).
    """
    return os.getenv("QUERY_WEB_AUTH_TOKEN", "")


@pytest.fixture(scope="session", autouse=True)
def _require_running_server(base_url: str) -> None:
    """Skip the whole e2e suite when the target server isn't reachable.

    Mirrors the existing smoke/integration test convention of skipping (not
    failing) when prerequisites aren't met, so `pytest tests` still passes in
    environments without the local stack running.
    """
    if not _healthcheck(base_url):
        pytest.skip(
            f"query-web is not reachable at {base_url}; "
            "start the local stack first (see README.md Local Development)."
        )
