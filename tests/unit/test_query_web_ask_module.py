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
    controls_context_cap: int | None = None
    temperature: float = 0.5
    top_p: float = 1.0
    thinking_mode: str = "balanced"
    controls_semantic: bool | None = None
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    include_graph_expansion: bool = False
    graph_expansion_depth: int | None = None
    graph_expansion_max_edges: int | None = None
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    max_completion_tokens: int | None = None
    evaluator_max_completion_tokens: int | None = None
    auth_token: str = ""


class _StrictAskRequest(BaseModel):
    question: str
    retrieve_k: int = 5
    controls_context_cap: int | None = None
    temperature: float = 0.5
    top_p: float = 1.0
    thinking_mode: str = "balanced"
    controls_semantic: bool | None = None
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    include_graph_expansion: bool = False
    graph_expansion_depth: int | None = None
    graph_expansion_max_edges: int | None = None
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    max_completion_tokens: int | None = None
    evaluator_max_completion_tokens: int | None = None
    auth_token: str = ""


class _AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
    controls_results: list[dict[str, Any]]
    controls_debug: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, Any] | None
    runtime_hints: dict[str, Any] | None = None
    graph_capabilities: dict[str, Any] | None = None
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
    load_calls: list[tuple[str, str, str]] = []

    def _load_conversation(user_id: str, conversation_id: str, *, correlation_id: str = "") -> Any:
        load_calls.append((user_id, conversation_id, correlation_id))
        return session_store.get(f"{user_id}:{conversation_id}")

    def _save_conversation(session: Any, *, correlation_id: str = "") -> None:
        save_calls.append((session, correlation_id))

    svc = SimpleNamespace(
        templates=_Templates(),
        config=SimpleNamespace(
            controls_semantic_default=True,
            search_index_name="idx",
            embedding_deployment="embed",
            query_deployment="query",
            evaluation_threshold=0.7,
            top_p=0.9,
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
        _load_calls=load_calls,
    )
    return svc


def _make_client(
    svc: SimpleNamespace, *, request_model: type[BaseModel] = _AskRequest
) -> TestClient:
    app = FastAPI()
    register_ask_endpoints(
        app,
        svc,
        ask_request_model=request_model,
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


def test_ask_get_redirects_to_home() -> None:
    svc = _make_svc()
    client = _make_client(svc)

    response = client.get("/ask", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/"


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
            "controls_context_cap": "99999",
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
    assert body["controls_context_cap"] == 3000
    assert body["temperature"] == 1.0
    assert body["controls_framework"] == "ism"
    assert len(session.messages) == 2
    assert len(svc._save_calls) == 1


def test_ask_post_propagates_correlation_id_to_conversation_io() -> None:
    svc = _make_svc()
    session = SimpleNamespace(messages=[], updated_at="")
    svc._session_store["user:s1:c1"] = session
    client = _make_client(svc)

    response = client.post(
        "/ask",
        data={
            "question": "trace me",
            "retrieve_k": "3",
            "temperature": "0.2",
            "auth_token": "ok",
            "session_id": "s1",
            "conversation_id": "c1",
        },
        headers={"x-correlation-id": "corr-ask-1"},
    )

    assert response.status_code == 200
    assert svc._load_calls[-1] == ("user:s1", "c1", "corr-ask-1")
    assert svc._save_calls[-1][1] == "corr-ask-1"


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


def test_ask_post_context_query_model_display_uses_ollama_model_in_local_mode(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:26b")

    svc = _make_svc()
    client = _make_client(svc)

    response = client.post(
        "/ask",
        data={
            "question": "q",
            "retrieve_k": "1",
            "temperature": "0.2",
            "auth_token": "ok",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_model_display"] == "gemma4:26b"


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

    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Unprocessable Content"
    assert body["detail"] == "Question must not be empty."
    assert body["instance"] == "/api/ask"


def test_api_ask_missing_question_returns_structured_validation_error() -> None:
    svc = _make_svc()
    client = _make_client(svc, request_model=_StrictAskRequest)

    response = client.post(
        "/api/ask",
        json={
            "auth_token": "ok",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Unprocessable Content"
    assert body["detail"] == "Invalid request payload."
    assert body["instance"] == "/api/ask"
    assert body["errors"]


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

    assert response.status_code == 401
    body = response.json()
    assert body["title"] == "Unauthorized"
    assert body["detail"] == "unauthorised"
    assert body["instance"] == "/api/ask"


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
    assert captured["controls_context_cap"] == 4


def test_api_ask_without_include_corpora_preserves_default_selection() -> None:
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
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == ""
    assert captured["evidence_corpora_include"] is None
    assert captured["evidence_corpora_exclude"] == []


def test_api_ask_explicit_empty_include_sets_warning() -> None:
    svc = _make_svc()
    captured: dict[str, Any] = {}

    def _run_rag(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "answer": "No relevant chunks were found in the index.",
            "results": [],
            "controls_results": [],
            "controls_debug": None,
            "evaluation": {"acceptable": False, "score": 0.0},
            "iterations": 1,
            "metrics": {"total_s": 0.1},
            "audit": {},
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
            "evidence_corpora_include": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == ""
    assert captured["evidence_corpora_include"] == []
    assert body["audit"]["warnings"] == [
        "No evidence corpora selected for retrieval. Provide evidence_corpora_include with one or more of: a, b, c."
    ]


def test_api_ask_legacy_include_falls_back_to_default_scope() -> None:
    svc = _make_svc()
    captured: dict[str, Any] = {}
    svc._normalise_evidence_corpora = lambda values: [
        v for v in (values or []) if v in {"a", "b", "c"}
    ]

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
            "audit": {},
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
            "evidence_corpora_include": ["legacy"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == ""
    assert captured["evidence_corpora_include"] is None
    assert body["audit"]["warnings"] == [
        "Unsupported evidence_corpora_include values were ignored. Using default corpora: a, b, c."
    ]


def test_api_ask_thinking_mode_applies_top_p_preset_when_not_overridden() -> None:
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
            "temperature": 1.0,
            "top_p": 1.0,
            "thinking_mode": "deep",
            "auth_token": "ok",
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == ""
    assert captured["temperature"] == 0.2
    assert captured["top_p"] == 0.85
    assert response.json()["runtime_hints"]["max_completion_tokens_source"] == (
        "thinking_mode_preset"
    )


def test_api_ask_runtime_hints_prefers_model_metadata_and_request_override(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MODEL_MAX_POSITION_EMBEDDINGS", "131072")

    svc = _make_svc()
    client = _make_client(svc)

    response = client.post(
        "/api/ask",
        json={
            "question": "hello",
            "retrieve_k": 5,
            "temperature": 0.2,
            "auth_token": "ok",
            "max_completion_tokens": 1800,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == ""
    assert body["runtime_hints"]["context_window_tokens_hint"] == 131072
    assert body["runtime_hints"]["context_window_source"] == "model_metadata"
    assert body["runtime_hints"]["max_completion_tokens_effective"] == 1800
    assert body["runtime_hints"]["max_completion_tokens_source"] == "request_override"


def test_api_ask_runtime_hints_caps_tokens_from_model_capabilities() -> None:
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
    svc._resolve_query_model_capabilities = lambda: {
        "source": "model_metadata",
        "context_window_tokens": 65536,
        "max_output_tokens": 900,
    }
    client = _make_client(svc)

    response = client.post(
        "/api/ask",
        json={
            "question": "hello",
            "retrieve_k": 5,
            "temperature": 0.2,
            "auth_token": "ok",
            "max_completion_tokens": 1800,
            "evaluator_max_completion_tokens": 1500,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == ""
    assert body["runtime_hints"]["context_window_tokens_hint"] == 65536
    assert body["runtime_hints"]["context_window_source"] == "model_metadata"
    assert body["runtime_hints"]["max_completion_tokens_effective"] == 900
    assert body["runtime_hints"]["max_completion_tokens_source"] == "model_metadata"
    assert body["runtime_hints"]["evaluator_max_completion_tokens_effective"] == 900
    assert captured["max_completion_tokens"] == 900
    assert captured["evaluator_max_completion_tokens"] == 900


def test_api_ask_forwards_graph_expansion_options() -> None:
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
            "include_graph_expansion": True,
            "graph_expansion_depth": 2,
            "graph_expansion_max_edges": 40,
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == ""
    assert captured["include_graph_expansion"] is True
    assert captured["graph_expansion_depth"] == 2
    assert captured["graph_expansion_max_edges"] == 40


def test_ask_console_path_allows_corpus_c_filters() -> None:
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
        "/ask",
        data={
            "question": "hello",
            "retrieve_k": "5",
            "temperature": "0.2",
            "controls_semantic": "false",
            "auth_token": "ok",
            "evidence_corpora_include": ["b", "c"],
            "evidence_corpora_exclude": ["legacy"],
        },
    )

    assert response.status_code == 200
    assert captured["evidence_corpora_include"] == ["b", "c"]
    assert captured["evidence_corpora_exclude"] == ["legacy"]


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

    assert response.status_code == 500
    body = response.json()
    assert body["title"] == "Internal Server Error"
    assert body["detail"] == "internal"
    assert body["instance"] == "/api/ask"
