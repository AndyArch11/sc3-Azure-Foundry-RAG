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

    Parameters
    ----------
    base_url:
        Ollama server base URL.  Defaults to ``OLLAMA_BASE_URL`` env var,
        then ``http://localhost:11434``.
    model:
        Ollama model name.  Defaults to ``OLLAMA_MODEL`` env var, then
        ``llama3``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        model: str | None = None,
        top_p: float = 1.0,
    ) -> None:
        env_base_url = os.getenv("OLLAMA_BASE_URL")
        env_model = os.getenv("OLLAMA_MODEL")
        self._base_url = (base_url or env_base_url or "http://localhost:11434").strip()
        self._model = (model or env_model or "llama3").strip()
        self._top_p = max(0.0, min(1.0, float(top_p)))

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run chat completion via Ollama or return an echo stub."""
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
            top_p=self._top_p,
            force_json=False,
        )

    def _echo(self, messages: list[dict[str, str]]) -> str:
        """Return the last user message as a stub response."""
        for msg in reversed(messages):
            if str(msg.get("role") or "") == "user":
                return f"[echo] {msg.get('content', '')}"
        return "[echo] (no user message)"

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``."""
        return self.chat_complete
