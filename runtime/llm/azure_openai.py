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

MAX_COMPLETION_TOKENS = 1400


class AzureOpenAILLMClient:
    """LLMClient backed by Azure OpenAI via the ``openai`` SDK.

    Attributes:
        _endpoint: The Azure OpenAI endpoint URL.
        _deployment: The Azure OpenAI deployment name.
        _credential: The credential object for Azure OpenAI.
        _temperature: The sampling temperature for LLM responses.
        _top_p: The top-p sampling parameter for LLM responses.
        _timeout: The timeout for LLM responses in seconds.

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
        """Initialise the AzureOpenAILLMClient.

        Args:
            endpoint: The Azure OpenAI endpoint URL.
            deployment: The Azure OpenAI deployment name.
            credential: The credential object for Azure OpenAI.
            temperature: The sampling temperature for LLM responses.
            top_p: The top-p sampling parameter for LLM responses.
            timeout: The timeout for LLM responses in seconds.
        """
        env_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        env_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self._endpoint = (endpoint or env_endpoint or "").strip()
        self._deployment = (deployment or env_deployment or "").strip()
        self._credential = credential
        self._temperature = max(0.0, min(1.0, float(temperature)))
        self._top_p = max(0.0, min(1.0, float(top_p)))
        self._timeout = max(1, int(timeout))

    def _get_credential(self) -> Any:
        """Get the credential object for Azure OpenAI.
        
        Returns:
            The credential object for Azure OpenAI."""
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
        """Get an access token for Azure OpenAI using the credential.
        
        Returns:
            An access token for Azure OpenAI.
        """
        cred = self._get_credential()
        token = cred.get_token("https://cognitiveservices.azure.com/.default")
        return str(token.token)

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Run a chat completion against Azure OpenAI and return the reply.

        Args:
            messages: A list of message dictionaries, each with "role" and "content" keys
        Returns:
            The content of the assistant's reply as a string.
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
            """Perform the actual chat completion request with the given temperature.

            The temperature is passed in so retry logic can override it when a
            backend rejects the configured value - some Foundry models only support a temperature of 1.0. 
            
            The completion budget is a fixed cap to match the shared assessment runtime policy and keep
            Azure adapter behaviour aligned with the rest of the stack.

            Args:
                temp: The sampling temperature for the request.
            Returns:
                The response object from the Azure OpenAI chat completion request.
            """
            request_kwargs: dict[str, Any] = {
                "model": self._deployment,
                "messages": cast(Any, messages),
                "max_completion_tokens": MAX_COMPLETION_TOKENS,
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
        """Return a plain callable wrapping ``chat_complete``.
        
        Returns:
            A callable that takes a list of messages and returns the assistant's reply.
        """
        return self.chat_complete
