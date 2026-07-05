"""Ollama local LLM client adapter (dev/local path).

Wraps the existing Ollama integration so it is accessible through the
provider-neutral ``LLMClient`` interface.  When Ollama is not available the
client falls back to echoing the last user message for offline testing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


class OllamaLLMClient:
    """LLMClient backed by a local Ollama server.

    Attributes:
        _base_url: The base URL for the Ollama server.
        _model: The Ollama model name to use for chat completions.
        _temperature: The temperature sampling parameter for LLM responses.
        _top_p: The top-p sampling parameter for LLM responses. 
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        model: str | None = None,
        temperature: float = 1.0,
        top_p: float = 1.0,
    ) -> None:
        """Initialise the OllamaLLMClient.
        Args:
            base_url: The base URL for the Ollama server (default: "http://localhost:11434").
            model: The Ollama model name to use for chat completions (default: "llama3").
            temperature: The temperature sampling parameter for LLM responses.
            top_p: The top-p sampling parameter for LLM responses.
        """
        env_base_url = os.getenv("OLLAMA_BASE_URL")
        env_model = os.getenv("OLLAMA_MODEL")
        self._base_url = (base_url or env_base_url or "http://localhost:11434").strip()
        self._model = (model or env_model or "llama3").strip()
        self._temperature = max(0.0, min(2.0, float(temperature)))
        self._top_p = max(0.0, min(1.0, float(top_p)))

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run chat completion via Ollama or return an echo stub.
        
        Args:
            messages: A list of message dicts, each with 'role' and 'content' keys.  Roles can be 'system', 'user', or 'assistant'.
        Returns:
            The assistant's reply as a string."""
        try:
            from runtime.assessment_orchestration.ollama_client import (
                is_ollama_available,
                ollama_chat_completion,
            )
        except ImportError:
            return self._echo(messages)

        if not is_ollama_available(self._base_url):
            logger.debug("Ollama not available at %s; using echo stub", self._base_url)
            return self._echo(messages)

        return ollama_chat_completion(
            messages,
            model=self._model,
            base_url=self._base_url,
            temperature=self._temperature,
            top_p=self._top_p,
            force_json=False,
        )

    def _echo(self, messages: list[dict[str, str]]) -> str:
        """Return the last user message as a stub response.
        
        Args:
            messages: A list of message dicts, each with 'role' and 'content' keys.  Roles can be 'system', 'user', or 'assistant'.
        Returns:
            The stub response as a string."""
        for msg in reversed(messages):
            if str(msg.get("role") or "") == "user":
                return f"[echo] {msg.get('content', '')}"
        return "[echo] (no user message)"

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``.
        
        Returns:
            A callable that takes a list of messages and returns the assistant's reply."""
        return self.chat_complete
