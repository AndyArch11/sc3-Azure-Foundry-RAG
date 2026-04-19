"""Unit tests for query_web/ask.py."""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.endpoints.ask import register_ask_endpoints


@dataclass
class _ConversationMessage:
    role: str
    content: str


class _AskRequest(BaseModel):
    question: str = ""
    retrieve_k: int = 5
    temperature: float = 0.5
    controls_semantic: bool | None = None
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    auth_token: str = ""


class _AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
    controls_results: list[dict[str, Any]]
    controls_debug: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, Any] | None
    audit: dict[str, Any] | None
    error: str


class _Templates:
    def TemplateResponse(
        self,
        request: Any,
        template_name: str,
        context: dict[str, Any],
        status_code: int = 200,
    ) -> JSONResponse:
        return JSONResponse({"template": template_name, **context}, status_code=status_code)


def _make_svc() -> SimpleNamespace:
    session_store: dict[str, Any] = {}
    save_calls: list[Any] = []

    def _load_conversation(user_id: str, conversation_id: str) -> Any:
        return session_store.get(f"{user_id}:{conversation_id}")

    def _save_conversation(session: Any) -> None:
        save_calls.append(session)

    svc = SimpleNamespace(
        templates=_Templates(),
        config=SimpleNamespace(
            controls_semantic_default=True,
            search_index_name="idx",
            embedding_deployment="embed",
            query_deployment="query",
            evaluation_threshold=0.7,
            auth_token="auth-required",
        ),
        _branding_ctx=lambda: {"brand": "x"},
        _get_user_id=lambda auth_token, session_id: f"user:{session_id or 'anon'}",
        _form_bool=lambda v, default=False: (
            str(v).strip().lower() in {"1", "true", "on", "yes"} if str(v).strip() else default
        ),
        _is_authorised_request=lambda auth_token, request: auth_token == "ok",
        _unauthorised_message=lambda request: "unauthorised",
        _normalise_controls_comparison_mode=lambda v: (v or "auto-detect").strip(),
        _normalise_framework_filter=lambda v: (v or "").strip().lower() or None,
        _normalise_evidence_corpora=lambda values: list(values or []),
        _build_feedback_context=lambda session: "feedback-context",
        _run_rag=lambda **kwargs: {
            "answer": "answer",
            "results": [{"id": "r1"}],
            "controls_results": [{"id": "c1"}],
            "controls_debug": {"x": 1},
            "evaluation": {"acceptable": True, "score": 1.0},
            "metrics": {"total_s": 0.1},
            "iterations": 2,
            "audit": {"evidence_corpus_filter_expr": "corpus eq 'b'"},
        },
        ConversationMessage=_ConversationMessage,
        _utc_now_iso=lambda: "2026-01-01T00:00:00+00:00",
        _load_conversation=_load_conversation,
        _save_conversation=_save_conversation,
        _INTERNAL_ERROR_MESSAGE="internal",
        _session_store=session_store,
        _save_calls=save_calls,
    )
    return svc


def _make_client(svc: SimpleNamespace) -> TestClient:
    app = FastAPI()
    register_ask_endpoints(
        app,
        svc,
        ask_request_model=_AskRequest,
        ask_response_model=_AskResponse,
    )
    return TestClient(app)


def test_ask_post_unauthorised_returns_401_template() -> None:
    svc = _make_svc()
    client = _make_client(svc)

    response = client.post(
        "/ask",
        data={
            "question": "q",
            "retrieve_k": "3",
            "temperature": "0.3",
            "controls_semantic": "true",
            "controls_framework": " NIST ",
            "controls_comparison_mode": "auto-detect",
            "advanced_mode": "true",
            "auth_token": "bad",
        },
    )

    assert response.status_code == 401
    body = response.json()
    assert body["template"] == "index.html"
    assert body["error"] == "unauthorised"
    assert body["auth_token"] == ""


def test_ask_post_authorised_updates_conversation_and_clamps_inputs() -> None:
    svc = _make_svc()
    session = SimpleNamespace(messages=[], updated_at="")
    svc._session_store["user:s1:c1"] = session
    client = _make_client(svc)

    response = client.post(
        "/ask",
        data={
            "question": "risk question",
            "retrieve_k": "999",
            "temperature": "99",
            "controls_semantic": "",
            "controls_framework": " ISM ",
            "controls_comparison_mode": "force_cross_framework_comparison",
            "evidence_corpora_include": ["b", "c"],
            "advanced_mode": "on",
            "auth_token": "ok",
            "session_id": "s1",
            "conversation_id": "c1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "answer"
    assert body["retrieve_k"] == 20
    assert body["temperature"] == 1.0
    assert body["controls_framework"] == "ism"
    assert len(session.messages) == 2
    assert len(svc._save_calls) == 1


def test_ask_post_exception_returns_internal_error() -> None:
    svc = _make_svc()
    svc._run_rag = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    client = _make_client(svc)

    response = client.post(
        "/ask",
        data={
            "question": "q",
            "retrieve_k": "1",
            "temperature": "0.1",
            "auth_token": "ok",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == "internal"
    assert body["answer"] == ""


def test_api_ask_empty_question_returns_validation_error_payload() -> None:
    svc = _make_svc()
    client = _make_client(svc)

    response = client.post(
        "/api/ask",
        json={
            "question": "   ",
            "retrieve_k": 5,
            "temperature": 0.2,
            "auth_token": "ok",
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == "Question must not be empty."


def test_api_ask_unauthorised_returns_error() -> None:
    svc = _make_svc()
    client = _make_client(svc)

    response = client.post(
        "/api/ask",
        json={
            "question": "hello",
            "retrieve_k": 5,
            "temperature": 0.2,
            "auth_token": "bad",
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == "unauthorised"


def test_api_ask_success_uses_default_controls_semantic_when_none() -> None:
    svc = _make_svc()
    captured: dict[str, Any] = {}

    def _run_rag(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "answer": "ok",
            "results": [],
            "controls_results": [],
            "controls_debug": None,
            "evaluation": {"acceptable": True, "score": 1.0},
            "iterations": 1,
            "metrics": {"total_s": 0.1},
            "audit": {"x": 1},
        }

    svc._run_rag = _run_rag
    client = _make_client(svc)

    response = client.post(
        "/api/ask",
        json={
            "question": "hello",
            "retrieve_k": 5,
            "temperature": 0.2,
            "auth_token": "ok",
            "controls_semantic": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == ""
    assert captured["controls_semantic"] is True


def test_api_ask_exception_returns_internal_error() -> None:
    svc = _make_svc()
    svc._run_rag = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    client = _make_client(svc)

    response = client.post(
        "/api/ask",
        json={
            "question": "hello",
            "retrieve_k": 5,
            "temperature": 0.2,
            "auth_token": "ok",
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == "internal"
