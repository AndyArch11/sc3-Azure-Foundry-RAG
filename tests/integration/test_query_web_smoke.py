from __future__ import annotations

import os
import time
from typing import Any

import pytest
import requests


pytestmark = [
    pytest.mark.integration,
    pytest.mark.private_network,
]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def base_url() -> str:
    url = (os.getenv("QUERY_WEB_BASE_URL") or "").strip().rstrip("/")
    if not url:
        pytest.skip("QUERY_WEB_BASE_URL is not set. Set it to the deployed query web URL.")
    return url


@pytest.fixture(scope="session")
def auth_token() -> str:
    return (os.getenv("QUERY_WEB_AUTH_TOKEN") or "").strip()


@pytest.fixture(scope="session")
def timeout_s() -> float:
    return float(os.getenv("QUERY_WEB_TIMEOUT_S", "30"))


@pytest.fixture(scope="session")
def verify_tls() -> bool:
    return not _bool_env("QUERY_WEB_INSECURE_TLS", default=False)


@pytest.fixture(scope="session")
def session(verify_tls: bool) -> requests.Session:
    s = requests.Session()
    s.verify = verify_tls
    return s


def _get_json(response: requests.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        body = response.text.strip()
        raise AssertionError(
            f"HTTP {response.status_code} for {response.request.method} {response.url}. "
            f"Response body: {body}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError("Expected JSON object response")
    return payload


@pytest.fixture(scope="session")
def config_payload(base_url: str, session: requests.Session, timeout_s: float) -> dict[str, Any]:
    resp = session.get(f"{base_url}/api/config", timeout=timeout_s)
    return _get_json(resp)


def _require_auth_token_if_enabled(config_payload: dict[str, Any], auth_token: str) -> None:
    if bool(config_payload.get("auth_enabled", False)) and not auth_token:
        pytest.skip(
            "query_web auth is enabled but QUERY_WEB_AUTH_TOKEN is not set. "
            "Set QUERY_WEB_AUTH_TOKEN to run integration tests."
        )


@pytest.fixture(scope="session")
def conversation_api_state(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    auth_token: str,
) -> dict[str, Any]:
    # Prefer OpenAPI path discovery when available.
    try:
        openapi_resp = session.get(f"{base_url}/openapi.json", timeout=timeout_s)
        if openapi_resp.status_code == 200:
            body = openapi_resp.json()
            if isinstance(body, dict):
                paths = body.get("paths")
                if isinstance(paths, dict) and "/api/conversations/new" in paths:
                    return {"available": True, "reason": "found in openapi"}
    except Exception:
        pass

    # Fallback: probe endpoint directly.
    try:
        probe = session.post(
            f"{base_url}/api/conversations/new",
            data={"auth_token": auth_token},
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        return {"available": False, "reason": f"probe request failed: {exc}"}

    if probe.status_code in {200, 401, 422}:
        return {"available": True, "reason": f"probe status {probe.status_code}"}
    if probe.status_code == 404:
        return {"available": False, "reason": "conversation routes not deployed (404)"}
    return {"available": False, "reason": f"unexpected probe status {probe.status_code}"}


def _require_conversation_api(conversation_api_state: dict[str, Any]) -> None:
    available = bool(conversation_api_state.get("available", False))
    if available:
        return

    strict = _bool_env("QUERY_WEB_REQUIRE_CONVERSATIONS", default=False)
    reason = str(conversation_api_state.get("reason", "unknown reason"))
    message = (
        "Conversation API is unavailable on this deployment. "
        f"Reason: {reason}. "
        "Deploy query_web image with conversation endpoints to enable these tests."
    )

    if strict:
        raise AssertionError(message)
    pytest.skip(message)


def test_health_endpoint(base_url: str, session: requests.Session, timeout_s: float) -> None:
    resp = session.get(f"{base_url}/health", timeout=timeout_s)
    payload = _get_json(resp)

    assert payload.get("status") == "ok"
    assert payload.get("service") == "rag-query-web"


def test_config_endpoint_shape(config_payload: dict[str, Any]) -> None:
    expected_keys = {
        "search_index_name",
        "embedding_deployment",
        "query_deployment",
        "evaluator_deployment",
        "default_top_k",
        "default_temperature",
        "evaluation_threshold",
        "auth_enabled",
    }
    assert expected_keys.issubset(set(config_payload.keys()))


@pytest.fixture()
def conversation_seed(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    config_payload: dict[str, Any],
    auth_token: str,
    conversation_api_state: dict[str, Any],
) -> dict[str, str]:
    _require_auth_token_if_enabled(config_payload, auth_token)
    _require_conversation_api(conversation_api_state)

    create_resp = session.post(
        f"{base_url}/api/conversations/new",
        data={"auth_token": auth_token},
        timeout=timeout_s,
    )
    create_payload = _get_json(create_resp)

    session_id = str(create_payload.get("session_id", "")).strip()
    conversation_id = str(create_payload.get("conversation_id", "")).strip()
    user_id = str(create_payload.get("user_id", "")).strip()

    assert session_id
    assert conversation_id
    assert user_id

    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
    }


def test_conversation_lifecycle(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    config_payload: dict[str, Any],
    auth_token: str,
    conversation_api_state: dict[str, Any],
    conversation_seed: dict[str, str],
) -> None:
    _require_auth_token_if_enabled(config_payload, auth_token)
    _require_conversation_api(conversation_api_state)

    probe_content = f"integration-smoke-{int(time.time())}"

    add_resp = session.post(
        f"{base_url}/api/conversations/{conversation_seed['conversation_id']}/message",
        data={
            "user_id": conversation_seed["user_id"],
            "role": "user",
            "content": probe_content,
            "auth_token": auth_token,
        },
        timeout=timeout_s,
    )
    add_payload = _get_json(add_resp)
    assert int(add_payload.get("message_id", 0)) >= 1

    history_resp = session.get(
        f"{base_url}/api/conversations/{conversation_seed['user_id']}/{conversation_seed['conversation_id']}",
        params={"auth_token": auth_token},
        timeout=timeout_s,
    )
    history_payload = _get_json(history_resp)

    messages = history_payload.get("messages")
    assert isinstance(messages, list)
    assert any(isinstance(m, dict) and m.get("content") == probe_content for m in messages)


def test_conversation_list_includes_new_thread(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    config_payload: dict[str, Any],
    auth_token: str,
    conversation_api_state: dict[str, Any],
    conversation_seed: dict[str, str],
) -> None:
    _require_auth_token_if_enabled(config_payload, auth_token)
    _require_conversation_api(conversation_api_state)

    resp = session.get(
        f"{base_url}/api/conversations/{conversation_seed['user_id']}",
        params={"auth_token": auth_token},
        timeout=timeout_s,
    )
    payload = _get_json(resp)

    conversations = payload.get("conversations")
    assert isinstance(conversations, list)
    assert any(
        isinstance(c, dict)
        and c.get("conversation_id") == conversation_seed["conversation_id"]
        for c in conversations
    )


def test_api_ask_optionally_runs(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    config_payload: dict[str, Any],
    auth_token: str,
) -> None:
    if not _bool_env("QUERY_WEB_RUN_API_ASK", default=False):
        pytest.skip("QUERY_WEB_RUN_API_ASK is false; skipping /api/ask integration call.")

    _require_auth_token_if_enabled(config_payload, auth_token)

    payload = {
        "question": os.getenv("QUERY_WEB_TEST_QUESTION", "What is secure-by-design?"),
        "retrieve_k": int(os.getenv("QUERY_WEB_TEST_RETRIEVE_K", "3")),
        "temperature": float(os.getenv("QUERY_WEB_TEST_TEMPERATURE", "1.0")),
        "auth_token": auth_token,
    }

    resp = session.post(
        f"{base_url}/api/ask",
        json=payload,
        timeout=max(timeout_s, 90.0),
    )
    body = _get_json(resp)

    assert "answer" in body
    assert "error" in body
    if body.get("error"):
        raise AssertionError(f"/api/ask returned error: {body['error']}")
