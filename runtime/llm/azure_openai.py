"""Azure OpenAI LLM client adapter.

Wraps the existing ``_chat_completion`` helper from
``runtime.assessment_orchestration.assessment_runtime`` so the same Azure
OpenAI call is available through the provider-neutral ``LLMClient`` interface.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from runtime.trace_context import outbound_trace_headers

logger = logging.getLogger(__name__)


class AzureOpenAILLMClient:
    """LLMClient backed by Azure OpenAI via the ``openai`` SDK.

    Parameters
    ----------
    endpoint:
        Azure OpenAI endpoint URL.  Falls back to ``AZURE_OPENAI_ENDPOINT``.
    deployment:
        Completion model deployment name.  Falls back to
        ``AZURE_OPENAI_DEPLOYMENT``.
    credential:
        Azure credential object (e.g. ``DefaultAzureCredential``).  When
        *None*, a ``DefaultAzureCredential`` is created lazily.
    temperature:
        Sampling temperature (0 - 1).
    top_p:
        Nucleus sampling probability mass (0 - 1).  When set below 1, the model
        samples only from the smallest token set whose cumulative probability
        reaches ``top_p``.
    timeout:
        HTTP timeout in seconds for each completion request.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        deployment: str | None = None,
        credential: Any = None,
        temperature: float = 0.0,
        top_p: float = 1.0,
        timeout: int = 45,
    ) -> None:
        env_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        env_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self._endpoint = (endpoint or env_endpoint or "").strip()
        self._deployment = (deployment or env_deployment or "").strip()
        self._credential = credential
        self._temperature = max(0.0, min(1.0, float(temperature)))
        self._top_p = max(0.0, min(1.0, float(top_p)))
        self._timeout = max(1, int(timeout))

    def _get_credential(self) -> Any:
        if self._credential is not None:
            return self._credential
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise RuntimeError(
                "azure-identity is required for AzureOpenAILLMClient but is not installed"
            ) from exc
        return DefaultAzureCredential()

    def _get_token(self) -> str:
        cred = self._get_credential()
        token = cred.get_token("https://cognitiveservices.azure.com/.default")
        return str(token.token)

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run a chat completion against Azure OpenAI and return the reply.

        Parameters
        ----------
        messages:
            OpenAI-style ``{"role": "...", "content": "..."}`` list.
        """
        try:
            from openai import AzureOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required for AzureOpenAILLMClient"
            ) from exc

        from typing import cast

        if not self._endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured")
        if not self._deployment:
            raise RuntimeError("AZURE_OPENAI_DEPLOYMENT is not configured")

        client = AzureOpenAI(
            api_key=self._get_token(),
            api_version="2024-08-01-preview",
            azure_endpoint=self._endpoint,
        )
        safe_temperature = self._temperature
        outbound_headers = outbound_trace_headers()

        def _do_create(temp: float) -> Any:
            request_kwargs: dict[str, Any] = {
                "model": self._deployment,
                "messages": cast(Any, messages),
                "max_completion_tokens": 1400,
                "temperature": temp,
                "top_p": self._top_p,
                "timeout": self._timeout,
            }
            if outbound_headers:
                request_kwargs["extra_headers"] = outbound_headers
            return client.chat.completions.create(**request_kwargs)

        try:
            response = _do_create(safe_temperature)
        except Exception as exc:
            message = str(exc).lower()
            should_retry = (
                safe_temperature != 1.0
                and "temperature" in message
                and any(
                    kw in message
                    for kw in ("must be 1", "only supports", "unsupported", "not supported", "invalid")
                )
            )
            should_retry_top_p = (
                self._top_p != 1.0
                and ("top_p" in message or "top p" in message or "topp" in message)
            )
            if not should_retry and not should_retry_top_p:
                raise
            if should_retry:
                response = _do_create(1.0)
            else:
                original_top_p = self._top_p
                self._top_p = 1.0
                try:
                    response = _do_create(safe_temperature)
                finally:
                    self._top_p = original_top_p

        return str(response.choices[0].message.content or "").strip()

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``."""
        return self.chat_complete
