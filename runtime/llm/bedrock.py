"""AWS Bedrock LLM client using the Converse API.

Uses ``bedrock-runtime`` via boto3.  Supports any Bedrock model that
implements the Converse API (Claude 3.x, Llama, Mistral, …).

Default model: ``anthropic.claude-3-5-sonnet-20241022-v2:0``
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

import requests

from runtime.trace_context import outbound_trace_headers

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"


def bedrock_embed_text(
    text: str,
    *,
    model_id: str,
    region_name: str | None = None,
    session: Any = None,
) -> list[float]:
    """Generate an embedding vector using AWS Bedrock Runtime.

    Parameters
    ----------
    text:
        Input text to embed.
    model_id:
        Bedrock embedding model ID (for example, ``amazon.titan-embed-text-v2:0``).
    region_name:
        AWS region override.
    session:
        Optional ``boto3.Session`` to reuse.
    """
    if not model_id.strip():
        raise RuntimeError("BEDROCK_EMBEDDING_MODEL_ID (or explicit model_id) is required")

    if session is None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for Bedrock embeddings but is not installed") from exc
        session = boto3.Session(region_name=region_name)

    client = session.client("bedrock-runtime")
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text}),
        contentType="application/json",
        accept="application/json",
    )

    body_stream = response.get("body")
    raw = body_stream.read() if hasattr(body_stream, "read") else body_stream
    if isinstance(raw, bytes):
        payload = json.loads(raw.decode("utf-8"))
    elif isinstance(raw, str):
        payload = json.loads(raw)
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise RuntimeError("Bedrock embedding response body was empty or invalid")

    vector = payload.get("embedding")
    if not isinstance(vector, list):
        by_type = payload.get("embeddingsByType")
        if isinstance(by_type, dict):
            candidate = by_type.get("float")
            if isinstance(candidate, list):
                vector = candidate
    if not isinstance(vector, list) or not vector:
        raise RuntimeError("Bedrock embedding response did not include a valid vector")

    return [float(v) for v in vector]


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


class BedrockMantleLLMClient:
    """LLMClient backed by Bedrock Mantle Anthropic-compatible Messages API.

    This path uses the Bedrock Mantle endpoint with API-key authentication:
    ``https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages``.
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        region_name: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1400,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model_id = model_id or os.getenv("BEDROCK_MODEL_ID") or _DEFAULT_MODEL
        self._temperature = max(0.0, min(1.0, float(temperature)))
        self._max_tokens = max(1, int(max_tokens))

        region = (region_name or os.getenv("AWS_REGION") or "").strip()
        env_base = (os.getenv("BEDROCK_MANTLE_BASE_URL") or "").strip()
        self._base_url = (base_url or env_base).rstrip("/")
        if not self._base_url:
            if not region:
                raise RuntimeError(
                    "AWS_REGION (or explicit region_name/base_url) is required for Bedrock Mantle"
                )
            self._base_url = f"https://bedrock-mantle.{region}.api.aws/anthropic"

        self._api_key = (api_key or os.getenv("BEDROCK_API_KEY") or "").strip()
        if not self._api_key:
            raise RuntimeError(
                "BEDROCK_API_KEY (or explicit api_key) is required for Bedrock Mantle"
            )

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run a Messages API call and return assistant text."""
        system_texts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = str(msg.get("role") or "user").strip().lower()
            content = str(msg.get("content") or "")
            if role == "system":
                if content.strip():
                    system_texts.append(content)
                continue

            if role not in {"user", "assistant"}:
                role = "user"
            anthropic_messages.append({"role": role, "content": content})

        if not anthropic_messages:
            return ""

        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": anthropic_messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if system_texts:
            payload["system"] = "\n\n".join(system_texts)

        response = requests.post(
            f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                **outbound_trace_headers(),
            },
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        body = response.json() if hasattr(response, "json") else {}

        content_list = body.get("content") or []
        texts: list[str] = []
        for block in content_list:
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            if text:
                texts.append(text)
        return " ".join(texts)

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``."""
        return self.chat_complete
