from __future__ import annotations

import types

import pytest

from runtime.assessment_orchestration import ollama_client


class _Resp:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _ReqExc("http error")

    def json(self) -> dict:
        return self._payload


class _ReqExc(Exception):
    pass


def _install_fake_requests(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_fn,
    post_fn,
) -> None:
    fake_requests = types.SimpleNamespace(
        get=get_fn,
        post=post_fn,
        exceptions=types.SimpleNamespace(RequestException=_ReqExc),
    )
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)


def test_resolve_base_url_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ignored.example:11434")

    assert ollama_client._resolve_base_url("http://explicit:11434/") == "http://explicit:11434"
    assert ollama_client._resolve_base_url(None) == "http://127.0.0.1:11434"

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama_client._resolve_base_url(None) == "http://ignored.example:11434"


def test_is_ollama_available_true_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: _Resp(status_code=200, payload={}),
        post_fn=lambda *args, **kwargs: _Resp(),
    )
    assert ollama_client.is_ollama_available("http://x:11434") is True

    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: (_ for _ in ()).throw(_ReqExc("down")),
        post_fn=lambda *args, **kwargs: _Resp(),
    )
    assert ollama_client.is_ollama_available("http://x:11434") is False


def test_ollama_chat_completion_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: _Resp(status_code=200, payload={}),
        post_fn=lambda endpoint, json, timeout: _Resp(
            payload={"message": {"content": '{"ok":true}'}}
        ),
    )

    result = ollama_client.ollama_chat_completion(
        [{"role": "user", "content": "hello"}],
        base_url="http://x:11434",
    )
    assert result == '{"ok":true}'


def test_ollama_chat_completion_raises_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: (_ for _ in ()).throw(_ReqExc("down")),
        post_fn=lambda *args, **kwargs: _Resp(),
    )

    with pytest.raises(RuntimeError, match="Ollama is not running"):
        ollama_client.ollama_chat_completion(
            [{"role": "user", "content": "hello"}],
            base_url="http://x:11434",
        )


def test_ollama_chat_completion_falls_back_to_generate_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _post(endpoint, json, timeout):
        calls.append(endpoint)
        if endpoint.endswith("/api/chat"):
            return _Resp(status_code=404, payload={})
        if endpoint.endswith("/api/generate"):
            return _Resp(payload={"response": '{"ok":true}'})
        return _Resp(status_code=500, payload={})

    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: _Resp(status_code=200, payload={}),
        post_fn=_post,
    )

    result = ollama_client.ollama_chat_completion(
        [{"role": "user", "content": "hello"}],
        base_url="http://x:11434",
    )
    assert result == '{"ok":true}'
    assert calls == ["http://x:11434/api/chat", "http://x:11434/api/generate"]


def test_ollama_chat_completion_retries_with_available_model_when_requested_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_models: list[str] = []

    def _post(endpoint, json, timeout):
        if endpoint.endswith("/api/chat"):
            attempted_models.append(json.get("model", ""))
            if json.get("model") == "llama3.2":
                return _Resp(status_code=404, payload={"error": "model 'llama3.2' not found"})
            if json.get("model") == "gemma3:27b":
                return _Resp(payload={"message": {"content": '{"ok":true}'}})
        return _Resp(status_code=500, payload={})

    def _get(url, timeout):
        if url.endswith("/api/tags"):
            return _Resp(
                status_code=200,
                payload={
                    "models": [
                        {"name": "nomic-embed-text"},
                        {"name": "gemma3:27b"},
                    ]
                },
            )
        return _Resp(status_code=404, payload={})

    _install_fake_requests(
        monkeypatch,
        get_fn=_get,
        post_fn=_post,
    )

    result = ollama_client.ollama_chat_completion(
        [{"role": "user", "content": "hello"}],
        base_url="http://x:11434",
        model="llama3.2",
    )
    assert result == '{"ok":true}'
    assert attempted_models == ["llama3.2", "gemma3:27b"]


def test_ollama_embedding_success_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: _Resp(status_code=200, payload={}),
        post_fn=lambda endpoint, json, timeout: _Resp(payload={"embeddings": [[0.1, 0.2, 0.3]]}),
    )
    vec = ollama_client.ollama_embedding("hello", base_url="http://x:11434")
    assert vec == [0.1, 0.2, 0.3]

    _install_fake_requests(
        monkeypatch,
        get_fn=lambda url, timeout: _Resp(status_code=200, payload={}),
        post_fn=lambda endpoint, json, timeout: _Resp(payload={"embeddings": []}),
    )
    with pytest.raises(ValueError, match="Invalid embeddings"):
        ollama_client.ollama_embedding("hello", base_url="http://x:11434")
