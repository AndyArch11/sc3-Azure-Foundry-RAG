"""Conversation management endpoints for storing and retrieving chat history."""

# This module intentionally prioritises endpoint/schema compatibility over strict
# lint conventions while wrappers and legacy payload contracts are maintained.
# pylint: disable=too-many-instance-attributes,missing-function-docstring
# pylint: disable=import-outside-toplevel,broad-exception-caught,too-many-positional-arguments

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

from query_web.utils import _utc_now_iso

logger = logging.getLogger(__name__)

_INTERNAL_ERROR_MESSAGE = "Internal server error; check logs for details."


@dataclass
class ConversationMessage:
    """A single message in a conversation."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: str = field(default_factory=_utc_now_iso)


@dataclass
class ResponseRating:
    """User rating and TODO feedback for a prior assistant response."""

    rating: int  # 1..5
    todo: str = ""
    assistant_timestamp: str = ""
    timestamp: str = field(default_factory=_utc_now_iso)


@dataclass
class ConversationSession:
    """Conversation session stored in CosmosDB."""

    session_id: str
    user_id: str  # auth_token hash or session token
    conversation_id: str  # unique per conversation
    messages: list[ConversationMessage] = field(default_factory=list)
    response_ratings: list[ResponseRating] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    evaluation_threshold: float = 0.72

    def to_dict(self) -> dict[str, Any]:
        # Sanitise ID by replacing hyphens from UUIDs with underscores for Cosmos compatibility
        sanitised_id = f"{self.user_id.replace('-', '_')}_{self.conversation_id.replace('-', '_')}"
        return {
            "id": sanitised_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "messages": [
                {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                for m in self.messages
            ],
            "response_ratings": [
                {
                    "rating": r.rating,
                    "todo": r.todo,
                    "assistant_timestamp": r.assistant_timestamp,
                    "timestamp": r.timestamp,
                }
                for r in self.response_ratings
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evaluation_threshold": self.evaluation_threshold,
            "type": "conversation",
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ConversationSession":
        messages = [
            ConversationMessage(
                role=m["role"], content=m["content"], timestamp=m.get("timestamp", _utc_now_iso())
            )
            for m in data.get("messages", [])
        ]
        response_ratings = [
            ResponseRating(
                rating=int(r.get("rating", 0)),
                todo=str(r.get("todo", "")),
                assistant_timestamp=str(r.get("assistant_timestamp", "")),
                timestamp=r.get("timestamp", _utc_now_iso()),
            )
            for r in data.get("response_ratings", [])
        ]
        return ConversationSession(
            session_id=data["session_id"],
            user_id=data["user_id"],
            conversation_id=data["conversation_id"],
            messages=messages,
            response_ratings=response_ratings,
            created_at=data.get("created_at", _utc_now_iso()),
            updated_at=data.get("updated_at", _utc_now_iso()),
            evaluation_threshold=data.get("evaluation_threshold", 0.72),
        )


def _get_user_id(auth_token: str, session_id: str) -> str:
    """Generate a stable user identifier from auth token or session ID."""
    import hashlib

    if auth_token.strip():
        return hashlib.sha256(auth_token.encode()).hexdigest()[:16]
    return session_id[:16]


def _load_conversation(user_id: str, conversation_id: str, container: Any) -> ConversationSession:
    """Load conversation from CosmosDB or create new one."""
    if not container:
        # Fallback to in-memory new conversation
        return ConversationSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
        )

    # Sanitise ID by replacing hyphens from UUIDs with underscores for Cosmos compatibility
    doc_id = f"{user_id.replace('-', '_')}_{conversation_id.replace('-', '_')}"
    try:
        doc = container.read_item(item=doc_id, partition_key=user_id)
        return ConversationSession.from_dict(doc)
    except CosmosResourceNotFoundError:
        # Conversation doesn't exist yet
        return ConversationSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        raise RuntimeError(f"Conversation persistence read failed: {exc}") from exc


def _save_conversation(session: ConversationSession, container: Any) -> None:
    """Save conversation to CosmosDB."""
    if not container:
        return
    try:
        container.upsert_item(session.to_dict())
    except Exception as exc:
        raise RuntimeError(f"Conversation persistence write failed: {exc}") from exc


def _build_feedback_context(session: ConversationSession, limit: int = 5) -> str:
    """Build short feedback context from recent user ratings/TODO notes."""
    if not session.response_ratings:
        return ""

    lines: list[str] = []
    for rating in session.response_ratings[-limit:]:
        todo_text = rating.todo.strip() or "No TODO provided"
        lines.append(f"- rating={rating.rating}/5; todo={todo_text}")

    return "Recent user feedback on prior answers:\n" + "\n".join(lines)


def register_conversations_endpoints(
    app: FastAPI,
    conversations_container: Any,
    _is_authorised_request: Any,
    _unauthorised_message: Any,
) -> None:
    """Register conversation management endpoints."""

    @app.get("/api/conversations/{user_id}")
    def get_conversations(request: Request, user_id: str, auth_token: str = "") -> JSONResponse:
        """List all conversations for a user."""
        if not _is_authorised_request(auth_token, request):
            return JSONResponse({"error": _unauthorised_message(request)}, status_code=401)

        if not conversations_container:
            return JSONResponse({"conversations": []})

        try:
            query = "SELECT c.session_id, c.conversation_id, c.created_at, c.updated_at, c.messages FROM c WHERE c.user_id = @user_id AND c.type = 'conversation' ORDER BY c.updated_at DESC"
            items = list(
                conversations_container.query_items(
                    query=query,
                    parameters=[{"name": "@user_id", "value": user_id}],
                )
            )
            return JSONResponse({"conversations": items})
        except Exception as exc:
            logger.exception("Failed to list conversations for user_id=%s: %s", user_id, exc)
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.get("/api/conversations/{user_id}/{conversation_id}")
    def get_conversation_history(
        request: Request, user_id: str, conversation_id: str, auth_token: str = ""
    ) -> JSONResponse:
        """Get full conversation history."""
        if not _is_authorised_request(auth_token, request):
            return JSONResponse({"error": _unauthorised_message(request)}, status_code=401)

        try:
            session = _load_conversation(user_id, conversation_id, conversations_container)
            return JSONResponse(
                {
                    "session_id": session.session_id,
                    "conversation_id": session.conversation_id,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "messages": [
                        {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                        for m in session.messages
                    ],
                    "response_ratings": [
                        {
                            "rating": r.rating,
                            "todo": r.todo,
                            "assistant_timestamp": r.assistant_timestamp,
                            "timestamp": r.timestamp,
                        }
                        for r in session.response_ratings
                    ],
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed to get conversation history for user_id=%s conversation_id=%s: %s",
                user_id,
                conversation_id,
                exc,
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/conversations/new")
    def create_conversation(request: Request, auth_token: str = Form("")) -> JSONResponse:
        """Create a new conversation session."""
        if not _is_authorised_request(auth_token, request):
            return JSONResponse({"error": _unauthorised_message(request)}, status_code=401)

        session_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())
        user_id = _get_user_id(auth_token, session_id)

        try:
            session = ConversationSession(
                session_id=session_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            _save_conversation(session, conversations_container)

            return JSONResponse(
                {
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                }
            )
        except Exception as exc:
            logger.exception("Failed to create conversation for user_id=%s: %s", user_id, exc)
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/conversations/{conversation_id}/message")
    def add_message_to_conversation(
        request: Request,
        conversation_id: str,
        user_id: str = Form(...),
        role: str = Form(...),
        content: str = Form(...),
        auth_token: str = Form(""),
    ) -> JSONResponse:
        """Add a message to a conversation."""
        if not _is_authorised_request(auth_token, request):
            return JSONResponse({"error": _unauthorised_message(request)}, status_code=401)

        try:
            session = _load_conversation(user_id, conversation_id, conversations_container)
            session.messages.append(ConversationMessage(role=role, content=content))
            session.updated_at = _utc_now_iso()
            _save_conversation(session, conversations_container)

            return JSONResponse(
                {
                    "message_id": len(session.messages),
                    "timestamp": session.messages[-1].timestamp,
                    "updated_at": session.updated_at,
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed to add message for user_id=%s conversation_id=%s: %s",
                user_id,
                conversation_id,
                exc,
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)

    @app.post("/api/conversations/{conversation_id}/rating")
    def add_response_rating(
        request: Request,
        conversation_id: str,
        user_id: str = Form(...),
        rating: int = Form(...),
        todo: str = Form(default=""),
        assistant_timestamp: str = Form(default=""),
        auth_token: str = Form(""),
    ) -> JSONResponse:
        """Store user rating/TODO feedback for a prior assistant response."""
        if not _is_authorised_request(auth_token, request):
            return JSONResponse({"error": _unauthorised_message(request)}, status_code=401)

        if rating < 1 or rating > 5:
            return JSONResponse({"error": "rating must be between 1 and 5"}, status_code=400)

        try:
            session = _load_conversation(user_id, conversation_id, conversations_container)

            if assistant_timestamp:
                has_target = any(
                    m.role == "assistant" and m.timestamp == assistant_timestamp
                    for m in session.messages
                )
                if not has_target:
                    return JSONResponse(
                        {"error": "assistant message not found for assistant_timestamp"},
                        status_code=404,
                    )

            session.response_ratings.append(
                ResponseRating(
                    rating=rating,
                    todo=todo.strip(),
                    assistant_timestamp=assistant_timestamp.strip(),
                )
            )
            session.updated_at = _utc_now_iso()
            _save_conversation(session, conversations_container)

            return JSONResponse(
                {
                    "ratings_count": len(session.response_ratings),
                    "updated_at": session.updated_at,
                }
            )
        except Exception as exc:
            logger.exception(
                "Failed to add response rating for user_id=%s conversation_id=%s: %s",
                user_id,
                conversation_id,
                exc,
            )
            return JSONResponse({"error": _INTERNAL_ERROR_MESSAGE}, status_code=500)
