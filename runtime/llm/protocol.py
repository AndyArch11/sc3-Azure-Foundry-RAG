"""LLMClient Protocol.

All LLM backend adapters implement this interface so callers are not coupled
to a specific provider.
"""

from __future__ import annotations

from typing import Callable, Protocol, TypeAlias, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """LLMClient — minimal chat completion interface.

    The single ``chat_complete`` method accepts an OpenAI-style messages list
    and returns the assistant's text reply.  This mirrors the
    ``Callable[[list[dict[str, str]]], str]`` pattern already used throughout
    ``runtime/assessment_orchestration/`` so the adapter can be dropped in
    without changing call sites.

    Attributes:
        chat_complete: Method to perform a chat completion request.
        as_callable: Method to return a plain callable wrapping ``chat_complete``.
        
    """

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant's text reply for *messages*.

        Args:
            messages: A list of message dicts, each with 'role' and 'content' keys.  Roles can be 'system', 'user', or 'assistant'.
        Returns:
            The assistant's reply as a string.
        """
        ...

    def as_callable(self) -> "ChatCompletionCallable":
        """Return a plain ``Callable[[list[dict]], str]`` wrapping this client.

        Useful for passing into code that expects the legacy callable form.

        Returns:
            A callable that takes a list of messages and returns the assistant's reply.
        """
        ...


# Type alias kept here for convenient import by call sites.
ChatCompletionCallable: TypeAlias = Callable[[list[dict[str, str]]], str]
