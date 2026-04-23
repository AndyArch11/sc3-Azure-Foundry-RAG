"""Unit tests for Foundry chat completion and conversation management."""

from __future__ import annotations

import os

# Set up required environment variables BEFORE importing app
os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from query_web.endpoints.conversations import (
    ConversationMessage,
    ConversationSession,
    ResponseRating,
)
from query_web.app import (
    _build_feedback_context,
    _get_user_id,
    _load_conversation,
    _save_conversation,
)


class TestConversationMessage:
    """Test ConversationMessage dataclass."""

    def test_create_message_with_defaults(self) -> None:
        msg = ConversationMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.timestamp  # Should have auto-generated timestamp

    def test_message_role_values(self) -> None:
        user_msg = ConversationMessage(role="user", content="Q1")
        asst_msg = ConversationMessage(role="assistant", content="A1")
        assert user_msg.role == "user"
        assert asst_msg.role == "assistant"


class TestConversationSession:
    """Test ConversationSession dataclass and serialisation."""

    def test_create_session(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )
        assert session.session_id == "sess-123"
        assert session.user_id == "user-456"
        assert session.conversation_id == "conv-789"
        assert session.messages == []
        assert session.created_at  # Auto-generated
        assert session.updated_at  # Auto-generated

    def test_session_to_dict(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )
        session.messages.append(ConversationMessage(role="user", content="Q"))

        doc = session.to_dict()
        assert doc["id"] == "user_456_conv_789"
        assert doc["session_id"] == "sess-123"
        assert doc["user_id"] == "user-456"
        assert doc["conversation_id"] == "conv-789"
        assert len(doc["messages"]) == 1
        assert doc["response_ratings"] == []
        assert doc["messages"][0]["role"] == "user"
        assert doc["messages"][0]["content"] == "Q"
        assert doc["type"] == "conversation"

    def test_session_rating_roundtrip(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )
        session.response_ratings.append(
            ResponseRating(
                rating=2, todo="Add mitigation checklist", assistant_timestamp="t-assist"
            )
        )

        doc = session.to_dict()
        restored = ConversationSession.from_dict(doc)

        assert len(restored.response_ratings) == 1
        assert restored.response_ratings[0].rating == 2
        assert restored.response_ratings[0].todo == "Add mitigation checklist"
        assert restored.response_ratings[0].assistant_timestamp == "t-assist"

    def test_session_from_dict_roundtrip(self) -> None:
        original = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
            evaluation_threshold=0.8,
        )
        original.messages.append(ConversationMessage(role="user", content="Q"))

        doc = original.to_dict()
        restored = ConversationSession.from_dict(doc)

        assert restored.session_id == original.session_id
        assert restored.user_id == original.user_id
        assert restored.conversation_id == original.conversation_id
        assert restored.evaluation_threshold == 0.8
        assert len(restored.messages) == 1
        assert restored.messages[0].role == "user"
        assert restored.messages[0].content == "Q"


class TestUserIdGeneration:
    """Test _get_user_id function."""

    def test_user_id_from_auth_token(self) -> None:
        # Hashed auth token should be consistent and deterministic
        user_id1 = _get_user_id("secret-token-123", "session-456")
        user_id2 = _get_user_id("secret-token-123", "session-789")

        # Same token → same user ID
        assert user_id1 == user_id2
        # Length is 16 (sha256[:16])
        assert len(user_id1) == 16
        assert all(c in "0123456789abcdef" for c in user_id1)

    def test_user_id_from_session_fallback(self) -> None:
        # Empty/blank auth token → use session ID
        user_id = _get_user_id("", "session-abcd1234efgh5678")
        assert user_id == "session-abcd1234"  # First 16 chars of session ID
        assert len(user_id) == 16

    def test_user_id_different_tokens(self) -> None:
        id1 = _get_user_id("token-aaa", "session-1")
        id2 = _get_user_id("token-bbb", "session-1")
        assert id1 != id2  # Different tokens → different user IDs


class TestConversationLoadSave:
    """Test conversation load/save with mocked CosmosDB."""

    def test_load_conversation_new(self) -> None:
        """Loading a non-existent conversation should create a new one."""
        with patch("query_web.app.conversations_container", None):
            session = _load_conversation("user-123", "conv-456")
            assert session.user_id == "user-123"
            assert session.conversation_id == "conv-456"
            assert session.messages == []

    def test_load_conversation_from_cosmos(self) -> None:
        """Loading existing conversation from CosmosDB."""
        mock_container = MagicMock()
        mock_container.read_item.return_value = {
            "session_id": "sess-123",
            "user_id": "user-456",
            "conversation_id": "conv-789",
            "messages": [
                {
                    "role": "user",
                    "content": "Q",
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                },
                {
                    "role": "assistant",
                    "content": "A",
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                },
            ],
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "type": "conversation",
        }

        with patch("query_web.app.conversations_container", mock_container):
            session = _load_conversation("user-456", "conv-789")
            assert session.user_id == "user-456"
            assert len(session.messages) == 2
            assert session.messages[0].role == "user"
            assert session.messages[1].role == "assistant"
            mock_container.read_item.assert_called_once()

    def test_load_conversation_not_found_returns_new(self) -> None:
        """If CosmosDB read raises exception, return new session."""
        mock_container = MagicMock()
        mock_container.read_item.side_effect = CosmosResourceNotFoundError(message="Not found")

        with patch("query_web.app.conversations_container", mock_container):
            session = _load_conversation("user-123", "conv-456")
            assert session.conversation_id == "conv-456"
            assert session.messages == []

    def test_save_conversation_to_cosmos(self) -> None:
        """Saving conversation should call upsert on container."""
        mock_container = MagicMock()
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )
        session.messages.append(ConversationMessage(role="user", content="Q"))

        with patch("query_web.app.conversations_container", mock_container):
            _save_conversation(session)
            mock_container.upsert_item.assert_called_once()
            call_args = mock_container.upsert_item.call_args
            saved_doc = call_args[0][0]  # First positional argument
            assert saved_doc["id"] == "user_456_conv_789"
            assert len(saved_doc["messages"]) == 1

    def test_save_conversation_noop_if_no_container(self) -> None:
        """If no container, save is a no-op."""
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )
        with patch("query_web.app.conversations_container", None):
            # Should not raise
            _save_conversation(session)


class TestConversationMessageHistory:
    """Test conversation message accumulation and serialisation."""

    def test_add_messages_to_session(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )

        session.messages.append(ConversationMessage(role="user", content="What is X?"))
        session.messages.append(ConversationMessage(role="assistant", content="X is..."))
        session.messages.append(ConversationMessage(role="user", content="Tell me more"))

        assert len(session.messages) == 3
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "assistant"
        assert session.messages[2].role == "user"

    def test_conversation_serialisation_preserves_order(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )

        for i, content in enumerate(["Q1", "A1", "Q2", "A2"]):
            role = "user" if i % 2 == 0 else "assistant"
            session.messages.append(ConversationMessage(role=role, content=content))

        doc = session.to_dict()
        restored = ConversationSession.from_dict(doc)

        assert len(restored.messages) == 4
        restored_contents = [m.content for m in restored.messages]
        assert restored_contents == ["Q1", "A1", "Q2", "A2"]


class TestConversationFeedbackContext:
    """Test feedback context generation from ratings/TODO notes."""

    def test_feedback_context_from_recent_ratings(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )
        session.response_ratings.append(ResponseRating(rating=4, todo="Keep concise"))
        session.response_ratings.append(ResponseRating(rating=2, todo="Need stronger grounding"))

        feedback = _build_feedback_context(session)

        assert "Recent user feedback on prior answers:" in feedback
        assert "rating=4/5; todo=Keep concise" in feedback
        assert "rating=2/5; todo=Need stronger grounding" in feedback

    def test_feedback_context_empty_when_no_ratings(self) -> None:
        session = ConversationSession(
            session_id="sess-123",
            user_id="user-456",
            conversation_id="conv-789",
        )

        assert _build_feedback_context(session) == ""
