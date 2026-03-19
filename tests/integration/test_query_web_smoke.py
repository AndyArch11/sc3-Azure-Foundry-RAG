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


@pytest.fixture(scope="session")
def openapi_paths(base_url: str, session: requests.Session, timeout_s: float) -> dict[str, Any] | None:
    try:
        openapi_resp = session.get(f"{base_url}/openapi.json", timeout=timeout_s)
    except requests.RequestException:
        return None

    if openapi_resp.status_code != 200:
        return None

    body = openapi_resp.json()
    if not isinstance(body, dict):
        return None

    paths = body.get("paths")
    if not isinstance(paths, dict):
        return None

    return paths


def _openapi_has_method(paths: dict[str, Any] | None, path: str, method: str) -> bool:
    if paths is None:
        return False
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return False
    return method.lower() in path_item


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
    openapi_paths: dict[str, Any] | None,
) -> dict[str, Any]:
    # Prefer OpenAPI path discovery when available.
    if _openapi_has_method(openapi_paths, "/api/conversations/new", "post"):
        return {"available": True, "reason": "found in openapi"}

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


@pytest.fixture(scope="session")
def rating_api_state(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    openapi_paths: dict[str, Any] | None,
) -> dict[str, Any]:
    # Prefer OpenAPI method discovery. This catches older deployments where
    # conversation routes exist but POST /rating was not added yet.
    if openapi_paths is not None:
        if _openapi_has_method(
            openapi_paths,
            "/api/conversations/{conversation_id}/rating",
            "post",
        ):
            return {"available": True, "reason": "found in openapi"}
        return {
            "available": False,
            "reason": "rating route missing from openapi",
        }

    # If OpenAPI is unavailable, treat as unknown and let runtime call decide.
    return {
        "available": False,
        "reason": "openapi unavailable; rating capability unknown",
    }


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


def _require_rating_api(rating_api_state: dict[str, Any]) -> None:
    available = bool(rating_api_state.get("available", False))
    if available:
        return

    strict = _bool_env("QUERY_WEB_REQUIRE_CONVERSATIONS", default=False)
    reason = str(rating_api_state.get("reason", "unknown reason"))
    message = (
        "Conversation rating API is unavailable on this deployment. "
        f"Reason: {reason}. "
        "Deploy a query_web image that includes POST /api/conversations/{conversation_id}/rating."
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


def test_conversation_rating_todo_and_follow_up_ask(
    base_url: str,
    session: requests.Session,
    timeout_s: float,
    config_payload: dict[str, Any],
    auth_token: str,
    conversation_api_state: dict[str, Any],
    rating_api_state: dict[str, Any],
    conversation_seed: dict[str, str],
) -> None:
    if not _bool_env("QUERY_WEB_RUN_API_ASK", default=False):
        pytest.skip("QUERY_WEB_RUN_API_ASK is false; skipping rating+follow-up /ask integration call.")

    _require_auth_token_if_enabled(config_payload, auth_token)
    _require_conversation_api(conversation_api_state)
    _require_rating_api(rating_api_state)

    # Seed an assistant message so rating can target a specific response timestamp.
    assistant_seed = f"assistant-seed-{int(time.time())}"
    seed_resp = session.post(
        f"{base_url}/api/conversations/{conversation_seed['conversation_id']}/message",
        data={
            "user_id": conversation_seed["user_id"],
            "role": "assistant",
            "content": assistant_seed,
            "auth_token": auth_token,
        },
        timeout=timeout_s,
    )
    seed_payload = _get_json(seed_resp)
    assistant_timestamp = str(seed_payload.get("timestamp", "")).strip()
    assert assistant_timestamp

    # Submit a rating + TODO feedback for that assistant response.
    todo_text = f"todo-improve-grounding-{int(time.time())}"
    rating_resp = session.post(
        f"{base_url}/api/conversations/{conversation_seed['conversation_id']}/rating",
        data={
            "user_id": conversation_seed["user_id"],
            "rating": 2,
            "todo": todo_text,
            "assistant_timestamp": assistant_timestamp,
            "auth_token": auth_token,
        },
        timeout=timeout_s,
    )
    if rating_resp.status_code in {404, 405}:
        strict = _bool_env("QUERY_WEB_REQUIRE_CONVERSATIONS", default=False)
        message = (
            "Conversation rating endpoint is not supported by the deployed app "
            f"(status={rating_resp.status_code}). "
            "Roll out the latest query_web image to include /rating support."
        )
        if strict:
            raise AssertionError(message)
        pytest.skip(message)
    rating_payload = _get_json(rating_resp)
    assert int(rating_payload.get("ratings_count", 0)) >= 1

    history_before_resp = session.get(
        f"{base_url}/api/conversations/{conversation_seed['user_id']}/{conversation_seed['conversation_id']}",
        params={"auth_token": auth_token},
        timeout=timeout_s,
    )
    history_before_payload = _get_json(history_before_resp)
    messages_before = history_before_payload.get("messages")
    assert isinstance(messages_before, list)
    before_count = len(messages_before)
    ratings_before = history_before_payload.get("response_ratings")
    assert isinstance(ratings_before, list)
    assert any(
        isinstance(r, dict)
        and int(r.get("rating", 0)) == 2
        and r.get("todo") == todo_text
        and r.get("assistant_timestamp") == assistant_timestamp
        for r in ratings_before
    )

    # Ask follow-up in same conversation so server can include ratings/TODO context.
    follow_up = f"Follow-up security question {int(time.time())}?"
    ask_resp = session.post(
        f"{base_url}/ask",
        data={
            "question": follow_up,
            "retrieve_k": int(os.getenv("QUERY_WEB_TEST_RETRIEVE_K", "3")),
            "temperature": float(os.getenv("QUERY_WEB_TEST_TEMPERATURE", "1.0")),
            "auth_token": auth_token,
            "session_id": conversation_seed["session_id"],
            "conversation_id": conversation_seed["conversation_id"],
        },
        timeout=max(timeout_s, 90.0),
    )
    if ask_resp.status_code >= 400:
        raise AssertionError(
            f"HTTP {ask_resp.status_code} for {ask_resp.request.method} {ask_resp.url}. "
            f"Response body: {ask_resp.text.strip()}"
        )

    history_after_resp = session.get(
        f"{base_url}/api/conversations/{conversation_seed['user_id']}/{conversation_seed['conversation_id']}",
        params={"auth_token": auth_token},
        timeout=timeout_s,
    )
    history_after_payload = _get_json(history_after_resp)
    messages_after = history_after_payload.get("messages")
    assert isinstance(messages_after, list)
    assert len(messages_after) >= before_count + 2
    assert any(isinstance(m, dict) and m.get("content") == follow_up for m in messages_after)
    ratings_after = history_after_payload.get("response_ratings")
    assert isinstance(ratings_after, list)
    assert any(
        isinstance(r, dict)
        and int(r.get("rating", 0)) == 2
        and r.get("todo") == todo_text
        and r.get("assistant_timestamp") == assistant_timestamp
        for r in ratings_after
    )
