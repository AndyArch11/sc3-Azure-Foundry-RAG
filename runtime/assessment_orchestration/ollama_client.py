"""Ollama local LLM client for development.

This module provides a compatibility layer to swap Azure OpenAI with local Ollama models.

Endpoint resolution (in priority order):
  1. Explicit base_url argument
  2. OLLAMA_HOST env var  (Ollama's native variable, e.g. http://host.docker.internal:11434)
  3. OLLAMA_BASE_URL env var
  4. http://host.docker.internal:11434  (works from inside a dev container / Docker)

Installation (on WSL host):
    curl https://ollama.ai/install.sh | sh
    OLLAMA_HOST=0.0.0.0 ollama serve  # bind to all interfaces so dev container can reach it

Recommended models for compliance report generation:
    ollama pull gemma3:27b        # best quality; fits in ~24 GB NVIDIA VRAM
    ollama pull llama4:scout      # strong alternative; 10M context, MoE architecture
    ollama pull nomic-embed-text  # lightweight embeddings

Usage:
    from runtime.assessment_orchestration.ollama_client import ollama_chat_completion

    response = ollama_chat_completion(
        messages=[
            {"role": "system", "content": "You are a compliance expert."},
            {"role": "user", "content": "Assess this control..."}
        ],
        model="gemma3:27b",
        temperature=1.0,
        timeout=180,
    )
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Candidate fallback URLs tried when no explicit base_url is passed.
# host.docker.internal resolves to the Windows/WSL host from inside Docker/devcontainer.
_DEFAULT_URLS = [
    "http://host.docker.internal:11434",
    "http://localhost:11434",
]


def _resolve_base_url(explicit: str | None = None) -> str:
    """Return the Ollama base URL to use, following the priority chain.

    Priority: explicit arg > OLLAMA_HOST env > OLLAMA_BASE_URL env > host.docker.internal default.
    """
    if explicit:
        return explicit.rstrip("/")
    # OLLAMA_HOST is Ollama's own env var; respect it first.
    ollama_host = os.environ.get("OLLAMA_HOST", "").strip().rstrip("/")
    if ollama_host:
        # Ollama allows bare "host:port" without scheme; normalise.
        if not ollama_host.startswith("http"):
            ollama_host = f"http://{ollama_host}"
        return ollama_host
    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return base_url
    return _DEFAULT_URLS[0]


def is_ollama_available(base_url: str | None = None) -> bool:
    """Check if Ollama is running and accessible."""
    url = _resolve_base_url(base_url)
    try:
        import requests  # type: ignore[import-untyped]

        response = requests.get(f"{url}/api/tags", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def ollama_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str = "gemma3:27b",
    base_url: str | None = None,
    temperature: float = 1.0,
    timeout: int = 240,
    force_json: bool = True,
    num_ctx: int = 65536,
) -> str:
    """Generate chat completion using local Ollama model.

    Args:
        messages: List of messages in OpenAI format [{"role": "system|user", "content": str}]
        model: Ollama model name (default: gemma3:27b)
        base_url: Ollama API endpoint — resolved from OLLAMA_HOST/OLLAMA_BASE_URL if not supplied
        temperature: Sampling temperature (0.0-2.0, default: 1.0)
        timeout: Request timeout in seconds (default: 240 — accounts for large Q8 models on long contexts)
        force_json: Pass format='json' to constrain output to valid JSON tokens (default: True).
            Strongly recommended for compliance report generation to reduce schema validation failures.
        num_ctx: Context window in tokens (default: 65536).
            If your pulled model/runtime supports it and memory allows, set
            OLLAMA_NUM_CTX=131072 to use a 128K context window.

    Returns:
        Generated text response (compatible with AzureOpenAI interface)

    Raises:
        RuntimeError: If Ollama is not available or request fails
    """
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests package is required for Ollama client") from exc

    resolved_url = _resolve_base_url(base_url)
    if not is_ollama_available(resolved_url):
        raise RuntimeError(
            f"Ollama is not running at {resolved_url}. "
            "On WSL host: OLLAMA_HOST=0.0.0.0 ollama serve"
        )

    endpoint = f"{resolved_url}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": max(0.0, min(2.0, float(temperature))),
            "num_ctx": num_ctx,
        },
    }
    if force_json:
        payload["format"] = "json"

    try:
        response = requests.post(endpoint, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Ollama chat request failed: {exc}") from exc

    try:
        result = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {exc}") from exc

    # Extract content from Ollama response format
    content = result.get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError(f"Ollama returned empty response: {result}")

    return content


def ollama_embedding(
    text: str,
    *,
    model: str = "nomic-embed-text",
    base_url: str | None = None,
    timeout: int = 60,
) -> list[float]:
    """Generate embeddings using local Ollama model.

    Args:
        text: Input text to embed
        model: Ollama embedding model name (default: nomic-embed-text)
        base_url: Ollama API endpoint (default: http://localhost:11434)
        timeout: Request timeout in seconds (default: 60)

    Returns:
        Embedding vector as list of floats

    Raises:
        RuntimeError: If Ollama is not available or request fails
        ValueError: If response contains invalid embedding

    Note:
        Ollama embeddings have different dimensionality than ada-002 (e.g., 384-768 vs 1536).
        This may affect search performance if indexes were built with Azure embeddings.
    """
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests package is required for Ollama client") from exc

    resolved_url = _resolve_base_url(base_url)
    if not is_ollama_available(resolved_url):
        raise RuntimeError(
            f"Ollama is not running at {resolved_url}. "
            "On WSL host: OLLAMA_HOST=0.0.0.0 ollama serve"
        )

    endpoint = f"{resolved_url}/api/embed"
    payload = {
        "model": model,
        "input": text,
    }

    try:
        response = requests.post(endpoint, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Ollama embed request failed ({resolved_url}): {exc}") from exc

    try:
        result = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {exc}") from exc

    embeddings = result.get("embeddings", None)
    if not embeddings or not isinstance(embeddings, list):
        raise ValueError(f"Invalid embeddings in Ollama response: {result}")

    if not embeddings[0] or not isinstance(embeddings[0], list):
        raise ValueError(f"Invalid embedding vector: {embeddings[0]}")

    return embeddings[0]
