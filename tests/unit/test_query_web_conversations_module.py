from __future__ import annotations

from unittest.mock import MagicMock, patch

from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from query_web.endpoints.conversations import (
    ConversationMessage,
    ConversationSession,
    ResponseRating,
    _build_feedback_context,
    _load_conversation,
    _save_conversation,
    register_conversations_endpoints,
)


def _build_client(*, container=None, authorised: bool = True) -> TestClient:
    app = FastAPI()
    register_conversations_endpoints(
        app,
        container,
        lambda auth_token, request: authorised,
        lambda request: "denied",
    )
    return TestClient(app)


def test_load_conversation_raises_runtime_error_on_unexpected_read_failure() -> None:
    container = MagicMock()
    container.read_item.side_effect = ValueError("broken read")

    try:
        _load_conversation("user-1", "conv-1", container)
    except RuntimeError as exc:
        assert "Conversation persistence read failed: broken read" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")


def test_load_conversation_uses_cosmos_safe_document_id() -> None:
    container = MagicMock()
    container.read_item.side_effect = CosmosResourceNotFoundError(message="missing")

    _load_conversation("user-123-456", "conv-789-000", container)

    container.read_item.assert_called_once_with(
        item="user_123_456_conv_789_000",
        partition_key="user-123-456",
    )


def test_save_conversation_raises_runtime_error_on_write_failure() -> None:
    container = MagicMock()
    container.upsert_item.side_effect = ValueError("broken write")
    session = ConversationSession(
        session_id="sess-1",
        user_id="user-1",
        conversation_id="conv-1",
    )

    try:
        _save_conversation(session, container)
    except RuntimeError as exc:
        assert "Conversation persistence write failed: broken write" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")


def test_feedback_context_respects_limit_and_missing_todo() -> None:
    session = ConversationSession(
        session_id="sess-1",
        user_id="user-1",
        conversation_id="conv-1",
        response_ratings=[
            ResponseRating(rating=5, todo="first"),
            ResponseRating(rating=3, todo=""),
            ResponseRating(rating=1, todo="last"),
        ],
    )

    feedback = _build_feedback_context(session, limit=2)

    assert "rating=5/5; todo=first" not in feedback
    assert "rating=3/5; todo=No TODO provided" in feedback
    assert "rating=1/5; todo=last" in feedback


def test_get_conversations_returns_unauthorised_when_auth_fails() -> None:
    client = _build_client(authorised=False)

    response = client.get("/api/conversations/user-1")

    assert response.status_code == 401
    assert response.json() == {"error": "denied"}


def test_get_conversations_returns_empty_list_without_container() -> None:
    client = _build_client(container=None)

    response = client.get("/api/conversations/user-1?auth_token=ok")

    assert response.status_code == 200
    assert response.json() == {"conversations": []}


def test_get_conversations_returns_items_from_container() -> None:
    container = MagicMock()
    container.query_items.return_value = [
        {
            "session_id": "sess-1",
            "conversation_id": "conv-1",
            "created_at": "2026-04-17T00:00:00+00:00",
            "updated_at": "2026-04-17T01:00:00+00:00",
            "messages": [{"role": "user", "content": "hello"}],
        }
    ]
    client = _build_client(container=container)

    response = client.get("/api/conversations/user-1?auth_token=ok")

    assert response.status_code == 200
    assert response.json()["conversations"][0]["conversation_id"] == "conv-1"
    container.query_items.assert_called_once()


def test_get_conversations_returns_internal_error_on_query_failure() -> None:
    container = MagicMock()
    container.query_items.side_effect = ValueError("boom")
    client = _build_client(container=container)

    response = client.get("/api/conversations/user-1?auth_token=ok")

    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error; check logs for details."}


def test_get_conversation_history_returns_serialised_session() -> None:
    container = MagicMock()
    container.read_item.return_value = ConversationSession(
        session_id="sess-1",
        user_id="user-1",
        conversation_id="conv-1",
        messages=[ConversationMessage(role="assistant", content="reply", timestamp="t-1")],
        response_ratings=[ResponseRating(rating=4, todo="tighten evidence", timestamp="t-2")],
        created_at="t-create",
        updated_at="t-update",
    ).to_dict()
    client = _build_client(container=container)

    response = client.get("/api/conversations/user-1/conv-1?auth_token=ok")

    body = response.json()
    assert response.status_code == 200
    assert body["session_id"] == "sess-1"
    assert body["messages"][0]["content"] == "reply"
    assert body["response_ratings"][0]["todo"] == "tighten evidence"


def test_get_conversation_history_returns_unauthorised_when_auth_fails() -> None:
    client = _build_client(authorised=False)

    response = client.get("/api/conversations/user-1/conv-1")

    assert response.status_code == 401
    assert response.json() == {"error": "denied"}


def test_get_conversation_history_returns_internal_error_on_load_failure() -> None:
    container = MagicMock()
    container.read_item.side_effect = ValueError("boom")
    client = _build_client(container=container)

    response = client.get("/api/conversations/user-1/conv-1?auth_token=ok")

    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error; check logs for details."}


def test_create_conversation_returns_unauthorised_when_auth_fails() -> None:
    client = _build_client(authorised=False)

    response = client.post("/api/conversations/new", data={"auth_token": "secret-token"})

    assert response.status_code == 401
    assert response.json() == {"error": "denied"}


def test_create_conversation_returns_internal_error_on_save_failure() -> None:
    container = MagicMock()
    container.upsert_item.side_effect = ValueError("boom")
    client = _build_client(container=container)

    with patch("query_web.endpoints.conversations.uuid.uuid4", side_effect=["sess-1", "conv-1"]):
        response = client.post("/api/conversations/new", data={"auth_token": "secret-token"})

    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error; check logs for details."}


def test_create_conversation_persists_and_returns_ids() -> None:
    container = MagicMock()
    client = _build_client(container=container)

    with patch("query_web.endpoints.conversations.uuid.uuid4", side_effect=["sess-1", "conv-1"]):
        response = client.post("/api/conversations/new", data={"auth_token": "secret-token"})

    body = response.json()
    assert response.status_code == 200
    assert body["session_id"] == "sess-1"
    assert body["conversation_id"] == "conv-1"
    assert len(body["user_id"]) == 16
    saved_doc = container.upsert_item.call_args[0][0]
    assert saved_doc["conversation_id"] == "conv-1"
    assert saved_doc["session_id"] == "sess-1"


def test_add_message_to_conversation_returns_unauthorised_when_auth_fails() -> None:
    client = _build_client(authorised=False)

    response = client.post(
        "/api/conversations/conv-1/message",
        data={
            "user_id": "user-1",
            "role": "assistant",
            "content": "reply",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"error": "denied"}


def test_add_message_to_conversation_returns_internal_error_on_load_failure() -> None:
    container = MagicMock()
    container.read_item.side_effect = ValueError("boom")
    client = _build_client(container=container)

    response = client.post(
        "/api/conversations/conv-1/message",
        data={
            "user_id": "user-1",
            "role": "assistant",
            "content": "reply",
            "auth_token": "ok",
        },
    )

    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error; check logs for details."}


def test_add_message_to_conversation_appends_message_and_updates_session() -> None:
    container = MagicMock()
    container.read_item.return_value = ConversationSession(
        session_id="sess-1",
        user_id="user-1",
        conversation_id="conv-1",
        messages=[ConversationMessage(role="user", content="hello", timestamp="t-1")],
        created_at="t-create",
        updated_at="t-old",
    ).to_dict()
    client = _build_client(container=container)

    with patch("query_web.endpoints.conversations._utc_now_iso", return_value="t-new"):
        response = client.post(
            "/api/conversations/conv-1/message",
            data={
                "user_id": "user-1",
                "role": "assistant",
                "content": "reply",
                "auth_token": "ok",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["message_id"] == 2
    assert body["updated_at"] == "t-new"
    assert body["timestamp"]
    saved_doc = container.upsert_item.call_args[0][0]
    assert len(saved_doc["messages"]) == 2
    assert saved_doc["messages"][-1]["content"] == "reply"


def test_add_response_rating_returns_unauthorised_when_auth_fails() -> None:
    client = _build_client(authorised=False)

    response = client.post(
        "/api/conversations/conv-1/rating",
        data={"user_id": "user-1", "rating": 5},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "denied"}


def test_add_response_rating_rejects_invalid_rating() -> None:
    client = _build_client(container=MagicMock())

    response = client.post(
        "/api/conversations/conv-1/rating",
        data={"user_id": "user-1", "rating": 0, "auth_token": "ok"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "rating must be between 1 and 5"}


def test_add_response_rating_returns_not_found_for_missing_assistant_timestamp() -> None:
    container = MagicMock()
    container.read_item.return_value = ConversationSession(
        session_id="sess-1",
        user_id="user-1",
        conversation_id="conv-1",
        messages=[ConversationMessage(role="assistant", content="reply", timestamp="t-known")],
    ).to_dict()
    client = _build_client(container=container)

    response = client.post(
        "/api/conversations/conv-1/rating",
        data={
            "user_id": "user-1",
            "rating": 4,
            "assistant_timestamp": "t-missing",
            "auth_token": "ok",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"error": "assistant message not found for assistant_timestamp"}


def test_add_response_rating_returns_internal_error_on_load_failure() -> None:
    container = MagicMock()
    container.read_item.side_effect = ValueError("boom")
    client = _build_client(container=container)

    response = client.post(
        "/api/conversations/conv-1/rating",
        data={"user_id": "user-1", "rating": 5, "auth_token": "ok"},
    )

    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error; check logs for details."}


def test_add_response_rating_appends_feedback_and_updates_session() -> None:
    container = MagicMock()
    container.read_item.return_value = ConversationSession(
        session_id="sess-1",
        user_id="user-1",
        conversation_id="conv-1",
        messages=[ConversationMessage(role="assistant", content="reply", timestamp="t-assistant")],
        created_at="t-create",
        updated_at="t-old",
    ).to_dict()
    client = _build_client(container=container)

    with patch("query_web.endpoints.conversations._utc_now_iso", return_value="t-new"):
        response = client.post(
            "/api/conversations/conv-1/rating",
            data={
                "user_id": "user-1",
                "rating": 5,
                "todo": "keep evidence tighter",
                "assistant_timestamp": "t-assistant",
                "auth_token": "ok",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body == {"ratings_count": 1, "updated_at": "t-new"}
    saved_doc = container.upsert_item.call_args[0][0]
    assert saved_doc["response_ratings"][0]["rating"] == 5
    assert saved_doc["response_ratings"][0]["todo"] == "keep evidence tighter"
    assert saved_doc["response_ratings"][0]["assistant_timestamp"] == "t-assistant"