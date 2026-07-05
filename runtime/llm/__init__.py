"""LLM client abstraction package.

``get_llm_client()`` dispatches on the ``CLOUD_PROVIDER`` env var (or the
``cloud_provider`` argument) and returns a ready-to-use ``LLMClient``:

  * ``azure`` / unset  → ``AzureOpenAILLMClient``
  * ``aws``            → ``BedrockLLMClient``
  * ``local`` / ``dev``→ ``OllamaLLMClient`` (dev stub - uses Ollama or echoes)
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
    top_p: float = 1.0,
    # AWS kwargs
    model_id: str | None = None,
    region_name: str | None = None,
    bedrock_session: Any = None,
    bedrock_api_mode: str | None = None,
    bedrock_api_key: str | None = None,
    bedrock_base_url: str | None = None,
    max_tokens: int | None = None,
    # Local kwargs
    ollama_base_url: str | None = None,
    ollama_model: str | None = None,
) -> LLMClient:
    """Return an ``LLMClient`` for the configured cloud provider.

    Args:
        cloud_provider: Optional override for the cloud provider (e.g., "azure", "aws", "local").
        openai_endpoint: Azure OpenAI endpoint URL (for Azure provider).
        deployment: Azure OpenAI deployment name (for Azure provider).
        credential: Credential object for Azure OpenAI (for Azure provider).
        temperature: Sampling temperature for LLM responses.
        top_p: Top-p sampling parameter for LLM responses.
        model_id: Model ID for AWS Bedrock (for AWS provider).
        region_name: AWS region name (for AWS provider).
        bedrock_session: Boto3 session for AWS Bedrock (for AWS provider).
        bedrock_api_mode: Mode for AWS Bedrock ("runtime" or "mantle").
        bedrock_api_key: API key for AWS Bedrock (for AWS provider).
        bedrock_base_url: Base URL for AWS Bedrock (for AWS provider).
        max_tokens: Maximum tokens for LLM responses (optional).
        ollama_base_url: Base URL for Ollama (for local provider).
        ollama_model: Model name for Ollama (for local provider). 

    Returns:
        An instance of ``LLMClient`` appropriate for the specified cloud provider.
    """
    provider_raw = cloud_provider if cloud_provider is not None else os.getenv("CLOUD_PROVIDER")
    provider = DEFAULT_CLOUD_PROVIDER_REGISTRY.get(provider_raw).provider

    if provider == "local":
        from .ollama import OllamaLLMClient

        return OllamaLLMClient(
            base_url=ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=ollama_model or os.getenv("OLLAMA_MODEL", "llama3"),
            top_p=top_p,
        )

    if provider == "aws":
        from .bedrock import BedrockLLMClient, BedrockMantleLLMClient

        mode_raw = (bedrock_api_mode or os.getenv("BEDROCK_API_MODE") or "runtime").strip().lower()
        mode = mode_raw or "runtime"

        if mode == "mantle":
            mantle_kwargs: dict[str, Any] = {
                "model_id": model_id,
                "region_name": region_name,
                "temperature": temperature,
                "top_p": top_p,
                "api_key": bedrock_api_key,
                "base_url": bedrock_base_url,
            }
            if max_tokens is not None:
                mantle_kwargs["max_tokens"] = max_tokens
            return BedrockMantleLLMClient(**mantle_kwargs)

        if mode != "runtime":
            raise ValueError(
                f"Unsupported BEDROCK_API_MODE '{mode}'. Expected 'runtime' or 'mantle'."
            )

        client_kwargs: dict[str, Any] = {
            "model_id": model_id,
            "session": bedrock_session,
            "region_name": region_name,
            "temperature": temperature,
            "top_p": top_p,
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
            top_p=top_p,
        )

    raise AssertionError(f"Unhandled provider '{provider}'")


__all__ = ["LLMClient", "get_llm_client"]
