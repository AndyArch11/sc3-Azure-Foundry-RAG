"""LLM backend factory for Azure vs local Ollama.

Environment variables:
    LLM_BACKEND: 'azure' (default) or 'ollama'
    OLLAMA_HOST: Ollama endpoint — Ollama's own env var (e.g. http://host.docker.internal:11434)
    OLLAMA_BASE_URL: Alternative endpoint override (OLLAMA_HOST takes precedence if both set)
    OLLAMA_CHAT_MODEL: Ollama chat model (default: gemma3:27b)
    OLLAMA_EMBED_MODEL: Ollama embedding model (default: nomic-embed-text)
    OLLAMA_NUM_CTX: Context window in tokens (default: 65536)
    OLLAMA_FORCE_JSON: Enable Ollama JSON mode (default: true)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from azure.identity import DefaultAzureCredential

    from .assessment_runtime import AssessmentRuntimeConfig

logger = logging.getLogger(__name__)


def get_llm_backend() -> str:
    """Get configured LLM backend: 'azure' or 'ollama'."""
    backend = os.environ.get("LLM_BACKEND", "azure").lower().strip()
    if backend not in ("azure", "ollama"):
        logger.warning(f"Invalid LLM_BACKEND '{backend}'; defaulting to 'azure'")
        return "azure"
    return backend


def create_chat_completion_fn(
    backend: str | None = None,
    *,
    config: AssessmentRuntimeConfig | None = None,
    credential: DefaultAzureCredential | None = None,
) -> Callable[[list[dict[str, str]]], str]:
    """Create a chat completion function for the specified backend.

    Args:
        backend: 'azure' or 'ollama' (default: from LLM_BACKEND env var)
        config: Azure runtime config (required if backend='azure')
        credential: Azure credential (required if backend='azure')

    Returns:
        Callable that accepts OpenAI-format messages and returns string response

    Example:
        chat_fn = create_chat_completion_fn()
        response = chat_fn([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"}
        ])
    """
    backend = backend or get_llm_backend()

    if backend == "ollama":
        from .ollama_client import _resolve_base_url, is_ollama_available, ollama_chat_completion

        ollama_url = _resolve_base_url(os.environ.get("OLLAMA_BASE_URL") or None)
        ollama_model = os.environ.get("OLLAMA_CHAT_MODEL", "gemma3:27b")

        if not is_ollama_available(ollama_url):
            logger.warning(
                f"Ollama not available at {ollama_url}; falling back to Azure. "
                f"Start with: ollama serve"
            )
            backend = "azure"
        else:
            logger.info(f"Using Ollama backend: {ollama_model} @ {ollama_url}")

            num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "65536"))
            force_json = os.environ.get("OLLAMA_FORCE_JSON", "true").lower() not in (
                "0",
                "false",
                "no",
            )

            def ollama_wrapper(messages: list[dict[str, str]]) -> str:
                return ollama_chat_completion(
                    messages,
                    model=ollama_model,
                    base_url=ollama_url,
                    temperature=1.0,
                    timeout=120,
                    force_json=force_json,
                    num_ctx=num_ctx,
                )

            return ollama_wrapper

    if backend == "azure":
        from . import assessment_runtime

        if config is None or credential is None:
            raise ValueError("config and credential required for Azure backend")

        logger.info(
            f"Using Azure backend: {config.query_deployment} " f"@ {config.openai_endpoint}"
        )

        def azure_wrapper(messages: list[dict[str, str]]) -> str:
            return assessment_runtime._chat_completion(
                messages, config=config, credential=credential
            )

        return azure_wrapper

    raise ValueError(f"Unknown LLM backend: {backend}")


def create_embedding_fn(
    backend: str | None = None,
    *,
    config: AssessmentRuntimeConfig | None = None,
    credential: DefaultAzureCredential | None = None,
) -> Callable[[str], list[float]]:
    """Create an embedding function for the specified backend.

    Args:
        backend: 'azure' or 'ollama' (default: from LLM_BACKEND env var)
        config: Azure runtime config (required if backend='azure')
        credential: Azure credential (required if backend='azure')

    Returns:
        Callable that accepts string text and returns embedding vector

    Note:
        For development (ollama backend), consider keyword-only search
        since Ollama embeddings have different dimensionality than ada-002.

    Example:
        embed_fn = create_embedding_fn()
        vector = embed_fn("What is machine learning?")
    """
    backend = backend or get_llm_backend()

    if backend == "ollama":
        from .ollama_client import _resolve_base_url, is_ollama_available, ollama_embedding

        ollama_url = _resolve_base_url(os.environ.get("OLLAMA_BASE_URL") or None)
        ollama_model = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

        if not is_ollama_available(ollama_url):
            logger.warning(
                f"Ollama not available at {ollama_url}; "
                f"falling back to Azure or returning zero vector"
            )
            backend = "azure"
        else:
            logger.info(f"Using Ollama embeddings: {ollama_model} @ {ollama_url}")

            def ollama_wrapper(text: str) -> list[float]:
                return ollama_embedding(
                    text,
                    model=ollama_model,
                    base_url=ollama_url,
                    timeout=60,
                )

            return ollama_wrapper

    if backend == "azure":
        from . import assessment_runtime

        if config is None or credential is None:
            raise ValueError("config and credential required for Azure backend")

        logger.info(
            f"Using Azure embeddings: {config.embedding_deployment} " f"@ {config.openai_endpoint}"
        )

        def azure_wrapper(text: str) -> list[float]:
            return assessment_runtime._embed_query(text, config=config, credential=credential)

        return azure_wrapper

    raise ValueError(f"Unknown LLM backend: {backend}")
