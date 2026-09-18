"""Unit tests for Playwright E2E target URL resolution."""

from __future__ import annotations

from tests.e2e import conftest


def test_resolve_base_url_falls_back_to_docker_host(monkeypatch) -> None:
    monkeypatch.delenv("QUERY_WEB_BASE_URL", raising=False)
    monkeypatch.setenv("QUERY_WEB_HOST_PORT", "18080")

    health_results = {
        "http://localhost:8080": False,
        "http://host.docker.internal:18080": True,
    }
    monkeypatch.setattr(conftest, "_healthcheck", health_results.__getitem__)

    assert conftest._resolve_base_url() == "http://host.docker.internal:18080"


def test_resolve_base_url_preserves_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_WEB_BASE_URL", "http://example.test/")
    monkeypatch.setattr(
        conftest,
        "_healthcheck",
        lambda base_url: (_ for _ in ()).throw(AssertionError(base_url)),
    )

    assert conftest._resolve_base_url() == "http://example.test"
