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

    Args:
        text: The input text to embed.
        model_id: The Bedrock model ID to use for embedding.
        region_name: Optional AWS region name (ignored if session is provided).
        session: Optional boto3 session (if None, a default session is created). 

    Returns:
        A list of floats representing the embedding vector.
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

    This path uses the Bedrock Converse API via boto3, with AWS credentials
    and region configured in the environment or via an explicit boto3 session.

    The Converse API supports any Bedrock model that implements the Converse interface, including Claude 3.x, Llama, Mistral, and others.

    Default model: ``anthropic.claude-3-5-sonnet-20241022-v2:0``

    Note:
        The Bedrock Converse API is not OpenAI-compatible; it uses a different request/response format.  
        This client wraps the Converse API and provides a simplified interface for chat completions.

    Attributes:
        _model_id: The Bedrock model ID to use for Converse requests.
        _temperature: Sampling temperature for LLM responses.
        _top_p: Top-p sampling parameter for LLM responses.
        _max_tokens: Maximum tokens for LLM responses.
        _client: The boto3 Bedrock Runtime client.  
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        session: Any = None,
        region_name: str | None = None,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 1400,
    ) -> None:
        """Initialise the BedrockLLMClient.
        
        Args:
            model_id: The Bedrock model ID to use for Converse requests.
            session: Optional boto3 session (if None, a default session is created).
            region_name: Optional AWS region name (ignored if session is provided).
            temperature: Sampling temperature for LLM responses.
            top_p: Top-p sampling parameter for LLM responses.
            max_tokens: Maximum tokens for LLM responses.
        """
        self._model_id = (
            model_id
            or os.getenv("BEDROCK_MODEL_ID")
            or _DEFAULT_MODEL
        )
        self._temperature = max(0.0, min(1.0, float(temperature)))
        self._top_p = max(0.0, min(1.0, float(top_p)))
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

        Args:
            messages: A list of message dicts, each with 'role' and 'content' keys.  Roles can be 'system', 'user', or 'assistant'.
        Returns:
            The assistant's reply as a string.
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
                "topP": self._top_p,
            },
        }
        if system_parts:
            kwargs["system"] = system_parts

        try:
            response = self._client.converse(**kwargs)
        except Exception as exc:
            err_text = str(exc).lower()
            if self._top_p == 1.0 or "top" not in err_text:
                raise
            kwargs["inferenceConfig"].pop("topP", None)
            response = self._client.converse(**kwargs)
        output = response.get("output") or {}
        message_obj = output.get("message") or {}
        content_list = message_obj.get("content") or []
        texts = [block.get("text") or "" for block in content_list if isinstance(block, dict)]
        return " ".join(t.strip() for t in texts if t.strip())

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``.
        
        Returns:
            A callable that takes a list of messages and returns the assistant's reply."""
        return self.chat_complete


class BedrockMantleLLMClient:
    """LLMClient backed by Bedrock Mantle OpenAI-compatible API.

    This path uses the Bedrock Mantle endpoint with API-key authentication:
    ``https://bedrock-mantle.{region}.api.aws/v1/chat/completions``.

    AWS is encouraging users to migrate from the Converse API to the Mantle API for OpenAI-compatible usage.
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        region_name: str | None = None,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 1400,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialise the BedrockLLMClient.

        Args:
            model_id: The Bedrock model ID to use for Converse requests.
            region_name: Optional AWS region name (ignored if base_url is provided).
            temperature: Sampling temperature for LLM responses.
            top_p: Top-p sampling parameter for LLM responses.
            max_tokens: Maximum tokens for LLM responses.
            api_key: API key for Bedrock Mantle (or set via BEDROCK_API_KEY).
            base_url: Base URL for Bedrock Mantle (or set via BEDROCK_MANTLE_BASE_URL).
        """
        self._model_id = model_id or os.getenv("BEDROCK_MODEL_ID") or _DEFAULT_MODEL
        self._temperature = max(0.0, min(1.0, float(temperature)))
        self._top_p = max(0.0, min(1.0, float(top_p)))
        self._max_tokens = max(1, int(max_tokens))

        region = (region_name or os.getenv("AWS_REGION") or "").strip()
        env_base = (os.getenv("BEDROCK_MANTLE_BASE_URL") or "").strip()
        self._base_url = (base_url or env_base).rstrip("/")
        if not self._base_url:
            if not region:
                raise RuntimeError(
                    "AWS_REGION (or explicit region_name/base_url) is required for Bedrock Mantle"
                )
            self._base_url = f"https://bedrock-mantle.{region}.api.aws"

        self._api_key = (api_key or os.getenv("BEDROCK_API_KEY") or "").strip()
        if not self._api_key:
            raise RuntimeError(
                "BEDROCK_API_KEY (or explicit api_key) is required for Bedrock Mantle"
            )

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run an OpenAI-compatible chat completions call and return assistant text.
        
        Args:
            messages: A list of message dicts, each with 'role' and 'content' keys.  Roles can be 'system', 'user', or 'assistant'.
        Returns:
            The assistant's reply as a string."""
        openai_messages: list[dict[str, Any]] = []
        has_non_system = False

        for msg in messages:
            role = str(msg.get("role") or "user").strip().lower()
            content = str(msg.get("content") or "")
            if role not in {"system", "user", "assistant"}:
                role = "user"
            if role != "system":
                has_non_system = True
            openai_messages.append({"role": role, "content": content})

        if not openai_messages or not has_non_system:
            return ""

        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": openai_messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "top_p": self._top_p,
        }

        response = requests.post(
            f"{self._base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                **outbound_trace_headers(),
            },
            json=payload,
            timeout=45,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = getattr(response, "status_code", None)
            if status_code == 404:
                raise RuntimeError(
                    "Bedrock Mantle returned HTTP 404 for /v1/chat/completions. "
                    "Verify BEDROCK_MANTLE_BASE_URL/BEDROCK_MODEL_ID and that the model "
                    "is supported in Bedrock Mantle for this region/account."
                ) from exc
            raise
        body = response.json() if hasattr(response, "json") else {}

        choices = body.get("choices") if isinstance(body, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                text = message.get("content")
                if isinstance(text, str):
                    return text.strip()
                if isinstance(text, list):
                    texts: list[str] = []
                    for item in text:
                        if isinstance(item, dict):
                            candidate = str(item.get("text") or "").strip()
                            if candidate:
                                texts.append(candidate)
                    if texts:
                        return " ".join(texts)
        return ""

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``.
        Returns:
            A callable that takes a list of messages and returns the assistant's reply."""
        return self.chat_complete
