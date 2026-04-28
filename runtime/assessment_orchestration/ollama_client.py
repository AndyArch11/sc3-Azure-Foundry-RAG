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
import time
from typing import Any

from runtime.outbound_instrumentation import request_with_instrumentation

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

        response = request_with_instrumentation(
            "GET",
            f"{url}/api/tags",
            logger=logger,
            timeout=2,
            system="ollama",
            operation="ollama_tags_check",
            request_callable=requests.get,
        )
        return response.status_code == 200
    except Exception:
        return False


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """Convert chat messages to a plain prompt for /api/generate fallback."""
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "user").strip().upper()
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        parts.append(f"{role}: {content}")
    parts.append("ASSISTANT:")
    return "\n\n".join(parts)


def _response_error_message(response: Any) -> str:
    """Extract the most useful error message from an HTTP response."""
    try:
        payload = response.json()
    except Exception:
        return str(getattr(response, "text", "") or "").strip()

    err = payload.get("error")
    if isinstance(err, dict):
        # OpenAI-compatible errors are often nested under error.message.
        return str(err.get("message") or err).strip()
    if err is not None:
        return str(err).strip()
    return str(payload).strip()


def _is_model_not_found(status_code: int, error_message: str) -> bool:
    msg = error_message.lower()
    return status_code == 404 and "model" in msg and "not found" in msg


def _list_available_models(resolved_url: str, timeout: int) -> list[str]:
    """Return model names reported by Ollama /api/tags."""
    try:
        import requests

        resp = request_with_instrumentation(
            "GET",
            f"{resolved_url}/api/tags",
            logger=logger,
            timeout=min(timeout, 10),
            system="ollama",
            operation="ollama_tags_list",
            request_callable=requests.get,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    models = payload.get("models")
    if not isinstance(models, list):
        return []

    names: list[str] = []
    for item in models:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _choose_fallback_chat_model(requested_model: str, available_models: list[str]) -> str | None:
    """Pick a non-embedding fallback model when requested model is unavailable."""
    requested = requested_model.strip().lower()
    for name in available_models:
        lower = name.lower()
        if lower == requested:
            return name

    for name in available_models:
        lower = name.lower()
        if "embed" in lower:
            continue
        if lower == requested:
            continue
        return name
    return None


def _chat_or_generate_once(
    *,
    requests: Any,
    resolved_url: str,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    timeout: int,
    force_json: bool,
    num_ctx: int,
) -> Any:
    """Issue one chat request with /api/generate fallback for endpoint compatibility."""
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

    response = request_with_instrumentation(
        "POST",
        endpoint,
        logger=logger,
        json=payload,
        timeout=timeout,
        system="ollama",
        operation="ollama_chat",
        request_callable=requests.post,
    )

    # Older Ollama builds may not implement /api/chat yet.
    # Fallback to /api/generate using a role-labelled prompt.
    if response.status_code == 404 and not _is_model_not_found(
        response.status_code, _response_error_message(response)
    ):
        generate_endpoint = f"{resolved_url}/api/generate"
        generate_payload: dict[str, Any] = {
            "model": model,
            "prompt": _messages_to_prompt(messages),
            "stream": False,
            "options": {
                "temperature": max(0.0, min(2.0, float(temperature))),
                "num_ctx": num_ctx,
            },
        }
        if force_json:
            generate_payload["format"] = "json"
        response = request_with_instrumentation(
            "POST",
            generate_endpoint,
            logger=logger,
            json=generate_payload,
            timeout=timeout,
            system="ollama",
            operation="ollama_generate",
            request_callable=requests.post,
        )

    return response


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

    timeout_override = os.getenv("OLLAMA_CHAT_TIMEOUT", "").strip()
    effective_timeout = timeout
    if timeout_override:
        try:
            effective_timeout = max(30, int(timeout_override))
        except ValueError:
            logger.warning(
                "Invalid OLLAMA_CHAT_TIMEOUT value '%s'; using timeout=%ss",
                timeout_override,
                timeout,
            )

    retries_raw = os.getenv("OLLAMA_CHAT_RETRIES", "1").strip()
    try:
        max_retries = max(0, min(3, int(retries_raw)))
    except ValueError:
        max_retries = 1

    response = None
    current_num_ctx = num_ctx
    for attempt in range(max_retries + 1):
        try:
            response = _chat_or_generate_once(
                requests=requests,
                resolved_url=resolved_url,
                messages=messages,
                model=model,
                temperature=temperature,
                timeout=effective_timeout,
                force_json=force_json,
                num_ctx=current_num_ctx,
            )
            break
        except requests.exceptions.ReadTimeout as exc:
            if attempt >= max_retries:
                raise RuntimeError(
                    "Ollama chat request timed out "
                    f"after {effective_timeout}s (attempts={max_retries + 1}, model={model})."
                ) from exc

            # Reduce context window after timeout to improve response latency on constrained hosts.
            next_num_ctx = max(8192, current_num_ctx // 2)
            if next_num_ctx < current_num_ctx:
                current_num_ctx = next_num_ctx

            backoff_s = min(8, 2**attempt)
            logger.warning(
                "Ollama read timeout after %ss (attempt %s/%s). Retrying in %ss with num_ctx=%s",
                effective_timeout,
                attempt + 1,
                max_retries + 1,
                backoff_s,
                current_num_ctx,
            )
            time.sleep(backoff_s)
            continue
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Ollama chat request failed: {exc}") from exc

    if response is None:
        raise RuntimeError("Ollama chat request failed before receiving a response.")

    if _is_model_not_found(response.status_code, _response_error_message(response)):
        available_models = _list_available_models(resolved_url, timeout)
        fallback_model = _choose_fallback_chat_model(model, available_models)
        if fallback_model and fallback_model.strip().lower() != model.strip().lower():
            logger.warning(
                "Ollama model '%s' not found; retrying chat request with available model '%s'",
                model,
                fallback_model,
            )
            try:
                response = _chat_or_generate_once(
                    requests=requests,
                    resolved_url=resolved_url,
                    messages=messages,
                    model=fallback_model,
                    temperature=temperature,
                    timeout=timeout,
                    force_json=force_json,
                    num_ctx=num_ctx,
                )
            except requests.exceptions.RequestException as exc:
                raise RuntimeError(f"Ollama fallback chat request failed: {exc}") from exc

        if _is_model_not_found(response.status_code, _response_error_message(response)):
            available = ", ".join(available_models) if available_models else "(none reported)"
            raise RuntimeError(
                f"Ollama model '{model}' not found at {resolved_url}. "
                f"Set OLLAMA_MODEL to an available chat model. Available models: {available}"
            )

    if response.status_code >= 400:
        error_message = _response_error_message(response) or str(
            getattr(response, "text", "") or ""
        )
        try:
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"Ollama chat request failed ({response.status_code}): {error_message or exc}"
            ) from exc

    try:
        result = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {exc}") from exc

    # Extract content from Ollama response format.
    # /api/chat returns message.content; /api/generate returns response.
    content = (
        str(result.get("message", {}).get("content") or "").strip()
        or str(result.get("response") or "").strip()
    )
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
        response = request_with_instrumentation(
            "POST",
            endpoint,
            logger=logger,
            json=payload,
            timeout=timeout,
            system="ollama",
            operation="ollama_embed",
            request_callable=requests.post,
        )
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
