"""Azure OpenAI LLM client adapter.

Wraps the existing ``_chat_completion`` helper from
``runtime.assessment_orchestration.assessment_runtime`` so the same Azure
OpenAI call is available through the provider-neutral ``LLMClient`` interface.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

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
        Sampling temperature (0 – 1).
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
        timeout: int = 45,
    ) -> None:
        env_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        env_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self._endpoint = (endpoint or env_endpoint or "").strip()
        self._deployment = (deployment or env_deployment or "").strip()
        self._credential = credential
        self._temperature = max(0.0, min(1.0, float(temperature)))
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

        def _do_create(temp: float) -> Any:
            return client.chat.completions.create(
                model=self._deployment,
                messages=cast(Any, messages),
                max_completion_tokens=1400,
                temperature=temp,
                timeout=self._timeout,
            )

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
            if not should_retry:
                raise
            response = _do_create(1.0)

        return str(response.choices[0].message.content or "").strip()

    def as_callable(self) -> Callable[[list[dict[str, str]]], str]:
        """Return a plain callable wrapping ``chat_complete``."""
        return self.chat_complete
