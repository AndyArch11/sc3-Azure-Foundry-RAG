from __future__ import annotations

import os
import types
from typing import Any

import pytest

from runtime.assessment_orchestration import assessment_runtime, dev_llms

# Minimal config and credential sentinels for tests that exercise the Ollama
# path (where config/credential are not actually consumed).
_CFG = assessment_runtime.AssessmentRuntimeConfig(
    search_endpoint="https://search.example",
    openai_endpoint="https://openai.example",
    query_deployment="gpt-test",
    embedding_deployment="embed-test",
)
_CRED: Any = object()  # duck-typed sentinel; not used by the Ollama path


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

    fn = dev_llms.create_chat_completion_fn("azure", config=_CFG, credential=_CRED)
    assert fn([{"role": "user", "content": "hi"}]) == "azure-ok"


def test_create_chat_completion_fn_ollama_available(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

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

    fn = dev_llms.create_chat_completion_fn("ollama", config=_CFG, credential=_CRED)
    assert fn([{"role": "user", "content": "hi"}]) == "ollama-ok"
    assert calls["kwargs"]["model"] == "gemma3:27b"
    assert calls["kwargs"]["num_ctx"] == 8192


def test_create_chat_completion_fn_ollama_model_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

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
    monkeypatch.delenv("OLLAMA_CHAT_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")

    fn = dev_llms.create_chat_completion_fn("ollama", config=_CFG, credential=_CRED)
    assert fn([{"role": "user", "content": "hi"}]) == "ollama-ok"
    assert calls["kwargs"]["model"] == "qwen2.5:14b"


def test_create_chat_completion_fn_ollama_uses_adaptive_num_ctx_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}

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
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.setattr(dev_llms, "_detect_host_resources", lambda: (6.0, 2))
    monkeypatch.setattr(dev_llms, "_detect_gpu_vram_gib", lambda: None)

    fn = dev_llms.create_chat_completion_fn("ollama", config=_CFG, credential=_CRED)
    assert fn([{"role": "user", "content": "hi"}]) == "ollama-ok"
    assert calls["kwargs"]["num_ctx"] == 8192


def test_create_chat_completion_fn_ollama_uses_gpu_vram_for_adaptive_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}

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
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.setattr(dev_llms, "_detect_host_resources", lambda: (64.0, 16))
    monkeypatch.setattr(dev_llms, "_detect_gpu_vram_gib", lambda: 12.0)

    fn = dev_llms.create_chat_completion_fn("ollama", config=_CFG, credential=_CRED)
    assert fn([{"role": "user", "content": "hi"}]) == "ollama-ok"
    assert calls["kwargs"]["num_ctx"] == 16384


def test_is_remote_ollama_url_local_addresses() -> None:
    for url in [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.0.0.2:11434",
        "http://0.0.0.0:11434",
        "http://[::1]:11434",  # RFC-3986 bracketed IPv6 loopback
    ]:
        assert dev_llms._is_remote_ollama_url(url) is False, url


def test_is_remote_ollama_url_remote_addresses() -> None:
    for url in [
        "http://host.docker.internal:11434",
        "http://wsl.localhost:11434",
        "http://192.168.1.50:11434",
        "http://ollama-server.example.com:11434",
    ]:
        assert dev_llms._is_remote_ollama_url(url) is True, url


def test_recommended_ollama_num_ctx_warns_when_remote_and_no_env(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(dev_llms, "_detect_host_resources", lambda: (16.0, 8))
    monkeypatch.setattr(dev_llms, "_detect_gpu_vram_gib", lambda: None)
    import logging

    with caplog.at_level(logging.WARNING, logger="runtime.assessment_orchestration.dev_llms"):
        ctx = dev_llms._recommended_ollama_num_ctx("http://host.docker.internal:11434")
    assert ctx == 16384
    assert "remote endpoint" in caplog.text
    assert "OLLAMA_NUM_CTX" in caplog.text


def test_recommended_ollama_num_ctx_no_warn_when_local(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(dev_llms, "_detect_host_resources", lambda: (16.0, 8))
    monkeypatch.setattr(dev_llms, "_detect_gpu_vram_gib", lambda: None)
    import logging

    with caplog.at_level(logging.WARNING, logger="runtime.assessment_orchestration.dev_llms"):
        dev_llms._recommended_ollama_num_ctx("http://localhost:11434")
    assert "remote endpoint" not in caplog.text


def test_detect_gpu_vram_gib_prefers_largest_across_vendor_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dev_llms, "_detect_nvidia_gpu_vram_gibs", lambda: [4.0, 6.0])
    monkeypatch.setattr(dev_llms, "_detect_linux_drm_gpu_vram_gibs", lambda: [8.0])
    assert dev_llms._detect_gpu_vram_gib() == 8.0


def test_recommended_ollama_ctx_uses_stronger_gpu_in_mixed_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dev_llms, "_detect_host_resources", lambda: (32.0, 12))
    monkeypatch.setattr(dev_llms, "_detect_nvidia_gpu_vram_gibs", lambda: [4.0])
    monkeypatch.setattr(dev_llms, "_detect_linux_drm_gpu_vram_gibs", lambda: [16.0])
    # 16 GiB GPU maps to 32K and host guardrail permits that value.
    assert dev_llms._recommended_ollama_num_ctx() == 32768


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

    fn = dev_llms.create_chat_completion_fn("ollama", config=_CFG, credential=_CRED)
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

    fn_ollama = dev_llms.create_embedding_fn("ollama", config=_CFG, credential=_CRED)
    assert fn_ollama("x") == [0.1, 0.2]

    fn_azure = dev_llms.create_embedding_fn("azure", config=_CFG, credential=_CRED)
    assert fn_azure("x") == [0.3, 0.4]


def test_create_embedding_fn_embedding_model_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def _embed(text, **kwargs):
        calls["kwargs"] = kwargs
        return [0.1, 0.2]

    fake_ollama = types.SimpleNamespace(
        _resolve_base_url=lambda explicit=None: "http://ollama:11434",
        is_ollama_available=lambda base_url=None: True,
        ollama_embedding=_embed,
    )
    monkeypatch.setitem(
        __import__("sys").modules, "runtime.assessment_orchestration.ollama_client", fake_ollama
    )
    monkeypatch.delenv("OLLAMA_EMBED_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "mxbai-embed-large")

    fn = dev_llms.create_embedding_fn("ollama", config=_CFG, credential=_CRED)
    assert fn("x") == [0.1, 0.2]
    assert calls["kwargs"]["model"] == "mxbai-embed-large"


def test_create_embedding_fn_invalid_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        dev_llms.create_embedding_fn("not-real", config=_CFG, credential=_CRED)


def test_create_chat_completion_fn_invalid_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        dev_llms.create_chat_completion_fn("not-real", config=_CFG, credential=_CRED)


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

    fn = dev_llms.create_embedding_fn("ollama", config=_CFG, credential=_CRED)
    assert fn("x") == [0.7, 0.8]


def test_create_embedding_fn_azure_requires_config_and_credential() -> None:
    with pytest.raises(ValueError, match="config and credential required"):
        dev_llms.create_embedding_fn("azure", config=None, credential=None)
