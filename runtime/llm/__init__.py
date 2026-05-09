"""LLM client abstraction package.

``get_llm_client()`` dispatches on the ``CLOUD_PROVIDER`` env var (or the
``cloud_provider`` argument) and returns a ready-to-use ``LLMClient``:

  * ``azure`` / unset  → ``AzureOpenAILLMClient``
  * ``aws``            → ``BedrockLLMClient``
  * ``local`` / ``dev``→ ``OllamaLLMClient`` (dev stub – uses Ollama or echoes)
"""

from __future__ import annotations

import os
from typing import Any

from runtime.provider_core import DEFAULT_CLOUD_PROVIDER_REGISTRY

from .protocol import LLMClient


def get_llm_client(
    cloud_provider: str | None = None,
    *,
    # Azure kwargs
    openai_endpoint: str | None = None,
    deployment: str | None = None,
    credential: Any = None,
    temperature: float = 0.0,
    # AWS kwargs
    model_id: str | None = None,
    region_name: str | None = None,
    bedrock_session: Any = None,
    max_tokens: int | None = None,
    # Local kwargs
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
) -> LLMClient:
    """Return an ``LLMClient`` for the configured cloud provider.

    Parameters
    ----------
    cloud_provider:
        Override the ``CLOUD_PROVIDER`` env var.
    openai_endpoint:
        Azure OpenAI endpoint URL (Azure path).
    deployment:
        Model deployment name (Azure: completion deployment; AWS: ignored in
        favour of ``model_id``).
    credential:
        Azure credential (Azure path, e.g. ``DefaultAzureCredential``).
    temperature:
        Sampling temperature (Azure/Bedrock paths).
    model_id:
        Bedrock model ID (AWS path, default ``anthropic.claude-3-5-sonnet-20241022-v2:0``).
    region_name:
        AWS region (AWS path).
    bedrock_session:
        ``boto3.Session`` to use (AWS path).
    ollama_base_url:
        Ollama base URL (local path, default ``http://localhost:11434``).
    ollama_model:
        Ollama model name (local path, default ``llama3``).
    """
    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "local":
        from .ollama import OllamaLLMClient

        return OllamaLLMClient(
            base_url=ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=ollama_model or os.getenv("OLLAMA_MODEL", "llama3"),
        )

    if provider == "aws":
        from .bedrock import BedrockLLMClient

        client_kwargs: dict[str, Any] = {
            "model_id": model_id,
            "session": bedrock_session,
            "region_name": region_name,
            "temperature": temperature,
        }
        if max_tokens is not None:
            client_kwargs["max_tokens"] = max_tokens
        return BedrockLLMClient(**client_kwargs)

    if provider == "azure":
        from .azure_openai import AzureOpenAILLMClient

        return AzureOpenAILLMClient(
            endpoint=openai_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            deployment=deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
            credential=credential,
            temperature=temperature,
        )

    raise AssertionError(f"Unhandled provider '{provider}'")


__all__ = ["LLMClient", "get_llm_client"]
