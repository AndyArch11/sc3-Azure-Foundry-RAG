"""LLMClient Protocol.

All LLM backend adapters implement this interface so callers are not coupled
to a specific provider.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """LLMClient — minimal chat completion interface.

    The single ``chat_complete`` method accepts an OpenAI-style messages list
    and returns the assistant's text reply.  This mirrors the
    ``Callable[[list[dict[str, str]]], str]`` pattern already used throughout
    ``runtime/assessment_orchestration/`` so the adapter can be dropped in
    without changing call sites.
    """

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant's text reply for *messages*.

        Parameters
        ----------
        messages:
            List of ``{"role": "...", "content": "..."}`` dicts in
            OpenAI/Bedrock Converse format.

        Returns
        -------
        str
            The model's reply (stripped of leading/trailing whitespace).
        """
        ...

    def as_callable(self) -> "ChatCompletionCallable":
        """Return a plain ``Callable[[list[dict]], str]`` wrapping this client.

        Useful for passing into code that expects the legacy callable form.
        """
        ...


# Type alias kept here for convenient import by call sites.
ChatCompletionCallable = "Callable[[list[dict[str, str]]], str]"
