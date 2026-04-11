from __future__ import annotations

import os
import types

import pytest

from runtime.assessment_orchestration import assessment_runtime, dev_llms


class _Cfg:
    query_deployment = "gpt-test"
    embedding_deployment = "embed-test"
    openai_endpoint = "https://openai.example"


def test_get_llm_backend_defaults_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert dev_llms.get_llm_backend() == "azure"

    monkeypatch.setenv("LLM_BACKEND", "bad-value")
    assert dev_llms.get_llm_backend() == "azure"


def test_create_chat_completion_fn_azure_requires_config_and_credential() -> None:
    with pytest.raises(ValueError, match="config and credential required"):
        dev_llms.create_chat_completion_fn("azure", config=None, credential=None)


def test_create_chat_completion_fn_azure_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        assessment_runtime, "_chat_completion", lambda messages, config, credential: "azure-ok"
    )

    fn = dev_llms.create_chat_completion_fn("azure", config=_Cfg(), credential=object())
    assert fn([{"role": "user", "content": "hi"}]) == "azure-ok"


def test_create_chat_completion_fn_ollama_available(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def _chat(messages, **kwargs):
        calls["kwargs"] = kwargs
        return "ollama-ok"

    fake_ollama = types.SimpleNamespace(
        _resolve_base_url=lambda explicit=None: "http://ollama:11434",
        is_ollama_available=lambda base_url=None: True,
        ollama_chat_completion=_chat,
    )
    monkeypatch.setitem(
        __import__("sys").modules, "runtime.assessment_orchestration.ollama_client", fake_ollama
    )
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", "gemma3:27b")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("OLLAMA_FORCE_JSON", "true")

    fn = dev_llms.create_chat_completion_fn("ollama", config=_Cfg(), credential=object())
    assert fn([{"role": "user", "content": "hi"}]) == "ollama-ok"
    assert calls["kwargs"]["model"] == "gemma3:27b"
    assert calls["kwargs"]["num_ctx"] == 8192


def test_create_chat_completion_fn_ollama_falls_back_to_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ollama = types.SimpleNamespace(
        _resolve_base_url=lambda explicit=None: "http://ollama:11434",
        is_ollama_available=lambda base_url=None: False,
        ollama_chat_completion=lambda *args, **kwargs: "unused",
    )
    monkeypatch.setitem(
        __import__("sys").modules, "runtime.assessment_orchestration.ollama_client", fake_ollama
    )
    monkeypatch.setattr(
        assessment_runtime,
        "_chat_completion",
        lambda messages, config, credential: "azure-fallback",
    )

    fn = dev_llms.create_chat_completion_fn("ollama", config=_Cfg(), credential=object())
    assert fn([{"role": "user", "content": "hi"}]) == "azure-fallback"


def test_create_embedding_fn_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ollama = types.SimpleNamespace(
        _resolve_base_url=lambda explicit=None: "http://ollama:11434",
        is_ollama_available=lambda base_url=None: True,
        ollama_embedding=lambda text, **kwargs: [0.1, 0.2],
    )
    monkeypatch.setitem(
        __import__("sys").modules, "runtime.assessment_orchestration.ollama_client", fake_ollama
    )
    monkeypatch.setattr(
        assessment_runtime, "_embed_query", lambda text, config, credential: [0.3, 0.4]
    )

    fn_ollama = dev_llms.create_embedding_fn("ollama", config=_Cfg(), credential=object())
    assert fn_ollama("x") == [0.1, 0.2]

    fn_azure = dev_llms.create_embedding_fn("azure", config=_Cfg(), credential=object())
    assert fn_azure("x") == [0.3, 0.4]


def test_create_embedding_fn_invalid_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        dev_llms.create_embedding_fn("not-real", config=_Cfg(), credential=object())


def test_create_chat_completion_fn_invalid_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        dev_llms.create_chat_completion_fn("not-real", config=_Cfg(), credential=object())


def test_create_embedding_fn_ollama_unavailable_falls_back_to_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ollama = types.SimpleNamespace(
        _resolve_base_url=lambda explicit=None: "http://ollama:11434",
        is_ollama_available=lambda base_url=None: False,
        ollama_embedding=lambda text, **kwargs: [9.9],
    )
    monkeypatch.setitem(
        __import__("sys").modules, "runtime.assessment_orchestration.ollama_client", fake_ollama
    )
    monkeypatch.setattr(
        assessment_runtime, "_embed_query", lambda text, config, credential: [0.7, 0.8]
    )

    fn = dev_llms.create_embedding_fn("ollama", config=_Cfg(), credential=object())
    assert fn("x") == [0.7, 0.8]


def test_create_embedding_fn_azure_requires_config_and_credential() -> None:
    with pytest.raises(ValueError, match="config and credential required"):
        dev_llms.create_embedding_fn("azure", config=None, credential=None)
