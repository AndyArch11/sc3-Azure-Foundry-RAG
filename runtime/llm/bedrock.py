"""AWS Bedrock LLM client using the Converse API.

Uses ``bedrock-runtime`` via boto3.  Supports any Bedrock model that
implements the Converse API (Claude 3.x, Llama, Mistral, …).

Default model: ``anthropic.claude-3-5-sonnet-20241022-v2:0``
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"


class BedrockLLMClient:
    """LLMClient backed by the AWS Bedrock Converse API.

    Parameters
    ----------
    model_id:
        Bedrock model ID.  Defaults to the ``BEDROCK_MODEL_ID`` env var, then
        ``anthropic.claude-3-5-sonnet-20241022-v2:0``.
    session:
        A ``boto3.Session`` (or compatible).  When *None* a default session is
        constructed via ``boto3.Session()``.
    region_name:
        AWS region.  Ignored when *session* is provided.
    temperature:
        Sampling temperature (0 - 1).
    max_tokens:
        Maximum tokens in the model reply.
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        session: Any = None,
        region_name: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1400,
    ) -> None:
        self._model_id = (
            model_id
            or os.getenv("BEDROCK_MODEL_ID")
            or _DEFAULT_MODEL
        )
        self._temperature = max(0.0, min(1.0, float(temperature)))
        self._max_tokens = max(1, int(max_tokens))

        if session is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for BedrockLLMClient but is not installed"
                ) from exc
            session = boto3.Session(region_name=region_name)

        self._client = session.client("bedrock-runtime")

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run a Converse API call and return the assistant text.

        Parameters
        ----------
        messages:
            OpenAI-style list of ``{"role": "...", "content": "..."}`` dicts.
            ``system`` role messages are extracted and passed as the Converse
            ``system`` parameter; all other messages are passed as the
            ``messages`` list.
        """
        system_parts: list[dict[str, Any]] = []
        converse_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            if role == "system":
                system_parts.append({"text": content})
            else:
                converse_messages.append(
                    {"role": role, "content": [{"text": content}]}
                )

        if not converse_messages:
            return ""

        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": converse_messages,
            "inferenceConfig": {
                "maxTokens": self._max_tokens,
                "temperature": self._temperature,
            },
        }
        if system_parts:
            kwargs["system"] = system_parts

        response = self._client.converse(**kwargs)
        output = response.get("output") or {}
        message = output.get("message") or {}
        content_list = message.get("content") or []
        texts = [block.get("text") or "" for block in content_list if isinstance(block, dict)]
        return " ".join(t.strip() for t in texts if t.strip())

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``."""
        return self.chat_complete
