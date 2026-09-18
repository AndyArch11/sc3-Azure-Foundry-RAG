"""Best-effort LLM token usage capture shared across provider adapters.

Provider adapters (Azure OpenAI, Bedrock Converse, Bedrock Mantle, Ollama)
record token usage via a context variable immediately after a chat
completion call, when the underlying API reports it. Callers that care about
token consumption (for example the Compliance Report pipeline) can pop the
last recorded usage right after invoking a chat completion.

When a provider does not report usage (for example an Ollama echo stub, or
an API response missing usage metadata), callers should fall back to
``estimate_tokens_from_text`` for an approximate count.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Token usage for a single chat completion call.

    Attributes:
        prompt_tokens: Number of tokens in the request/prompt.
        completion_tokens: Number of tokens in the generated response.
        total_tokens: Total tokens consumed by the call.
        estimated: True when the counts are a heuristic approximation rather
            than an exact count reported by the provider.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated: bool = False


_LAST_USAGE: ContextVar["TokenUsage | None"] = ContextVar("_LAST_USAGE", default=None)


def record_token_usage(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int | None = None,
    estimated: bool = False,
) -> None:
    """Record token usage for the most recently completed chat call.

    Args:
        prompt_tokens: Number of tokens in the request/prompt.
        completion_tokens: Number of tokens in the generated response.
        total_tokens: Total tokens consumed (defaults to prompt + completion).
        estimated: True when the counts are a heuristic approximation.
    """
    resolved_total = (
        int(total_tokens) if total_tokens is not None else int(prompt_tokens) + int(completion_tokens)
    )
    _LAST_USAGE.set(
        TokenUsage(
            prompt_tokens=max(0, int(prompt_tokens)),
            completion_tokens=max(0, int(completion_tokens)),
            total_tokens=max(0, resolved_total),
            estimated=estimated,
        )
    )


def pop_last_token_usage() -> TokenUsage | None:
    """Return and clear the most recently recorded token usage, if any.

    Returns:
        The last recorded ``TokenUsage``, or ``None`` if nothing was recorded
        since the last call (or the provider does not report usage).
    """
    usage = _LAST_USAGE.get()
    _LAST_USAGE.set(None)
    return usage


def estimate_tokens_from_text(text: str) -> int:
    """Heuristically estimate the token count for a block of text.

    Uses the common approximation of ~4 characters per token for English
    text, which is reasonable across most tokenizers when exact counts are
    unavailable.

    Args:
        text: The text to estimate a token count for.

    Returns:
        An approximate token count (minimum of 1 for non-empty text).
    """
    if not text:
        return 0
    return max(1, round(len(text) / 4))
