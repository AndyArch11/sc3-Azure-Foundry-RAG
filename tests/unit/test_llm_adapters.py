"""Unit tests for the LLM client adapters and factory."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtime.llm import get_llm_client
from runtime.llm.azure_openai import AzureOpenAILLMClient
from runtime.llm.bedrock import BedrockLLMClient
from runtime.llm.ollama import OllamaLLMClient
from runtime.llm.protocol import LLMClient
from runtime.trace_context import scoped_trace_context

# ---------------------------------------------------------------------------
# Protocol structural check
# ---------------------------------------------------------------------------


class TestLLMClientProtocol:
    def test_bedrock_satisfies_protocol(self) -> None:
        fake_session = MagicMock()
        client = BedrockLLMClient("anthropic.claude-3-5-sonnet-20241022-v2:0", session=fake_session)
        assert isinstance(client, LLMClient)

    def test_azure_satisfies_protocol(self) -> None:
        client = AzureOpenAILLMClient(
            endpoint="https://example.openai.azure.com", credential=MagicMock()
        )
        assert isinstance(client, LLMClient)

    def test_ollama_satisfies_protocol(self) -> None:
        client = OllamaLLMClient()
        assert isinstance(client, LLMClient)


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


class TestLLMClientFactory:
    def test_default_is_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
        client = get_llm_client(
            openai_endpoint="https://x.openai.azure.com",
            credential=MagicMock(),
        )
        assert isinstance(client, AzureOpenAILLMClient)

    def test_explicit_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        client = get_llm_client(
            openai_endpoint="https://x.openai.azure.com",
            credential=MagicMock(),
        )
        assert isinstance(client, AzureOpenAILLMClient)

    def test_aws_returns_bedrock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        fake_session = MagicMock()
        client = get_llm_client(bedrock_session=fake_session)
        assert isinstance(client, BedrockLLMClient)

    def test_local_returns_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        client = get_llm_client()
        assert isinstance(client, OllamaLLMClient)

    def test_dev_alias_returns_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "dev")
        client = get_llm_client()
        assert isinstance(client, OllamaLLMClient)

    def test_cloud_provider_arg_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        client = get_llm_client(cloud_provider="local")
        assert isinstance(client, OllamaLLMClient)

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        with pytest.raises(ValueError, match="Unsupported"):
            get_llm_client()

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "  local  ")
        client = get_llm_client()
        assert isinstance(client, OllamaLLMClient)


# ---------------------------------------------------------------------------
# BedrockLLMClient
# ---------------------------------------------------------------------------


class TestBedrockLLMClient:
    def _make_client(
        self, converse_response: dict[str, Any] | None = None
    ) -> tuple[BedrockLLMClient, MagicMock]:
        fake_session = MagicMock()
        fake_bedrock = MagicMock()
        fake_session.client.return_value = fake_bedrock
        if converse_response is not None:
            fake_bedrock.converse.return_value = converse_response
        client = BedrockLLMClient("anthropic.claude-3-5-sonnet-20241022-v2:0", session=fake_session)
        return client, fake_bedrock

    def _converse_resp(self, text: str) -> dict[str, Any]:
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": text}],
                }
            }
        }

    def test_chat_complete_returns_text(self) -> None:
        client, _ = self._make_client(self._converse_resp("Hello from Claude"))
        result = client.chat_complete([{"role": "user", "content": "Hi"}])
        assert result == "Hello from Claude"

    def test_system_messages_extracted(self) -> None:
        client, fake_bedrock = self._make_client(self._converse_resp("ok"))
        client.chat_complete(
            [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Tell me something"},
            ]
        )
        call_kwargs = fake_bedrock.converse.call_args.kwargs
        assert "system" in call_kwargs
        assert call_kwargs["system"][0]["text"] == "You are a helpful assistant."

    def test_converse_called_with_correct_model(self) -> None:
        client, fake_bedrock = self._make_client(self._converse_resp("ok"))
        client.chat_complete([{"role": "user", "content": "test"}])
        assert (
            fake_bedrock.converse.call_args.kwargs["modelId"]
            == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        )

    def test_empty_messages_returns_empty_string(self) -> None:
        client, fake_bedrock = self._make_client()
        result = client.chat_complete([])
        assert result == ""
        fake_bedrock.converse.assert_not_called()

    def test_temperature_clamped_to_valid_range(self) -> None:
        fake_session = MagicMock()
        client = BedrockLLMClient(session=fake_session, temperature=1.5)
        assert client._temperature == 1.0
        client2 = BedrockLLMClient(session=fake_session, temperature=-0.5)
        assert client2._temperature == 0.0

    def test_as_callable_returns_callable(self) -> None:
        client, _ = self._make_client(self._converse_resp("hi"))
        fn = client.as_callable()
        result = fn([{"role": "user", "content": "hey"}])
        assert result == "hi"

    def test_default_model_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BEDROCK_MODEL_ID", "amazon.titan-text-express-v1")
        fake_session = MagicMock()
        client = BedrockLLMClient(session=fake_session)
        assert client._model_id == "amazon.titan-text-express-v1"

    def test_missing_boto3_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(RuntimeError, match="boto3"):
                BedrockLLMClient("model-id")

    def test_multi_block_content_joined(self) -> None:
        resp = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Hello"}, {"text": "World"}],
                }
            }
        }
        client, _ = self._make_client(resp)
        result = client.chat_complete([{"role": "user", "content": "hi"}])
        assert "Hello" in result
        assert "World" in result


# ---------------------------------------------------------------------------
# AzureOpenAILLMClient
# ---------------------------------------------------------------------------


class TestAzureOpenAILLMClient:
    def _make_client(self, reply: str = "Azure reply") -> tuple[AzureOpenAILLMClient, MagicMock]:
        fake_cred = MagicMock()
        fake_cred.get_token.return_value = MagicMock(token="test-token")
        client = AzureOpenAILLMClient(
            endpoint="https://test.openai.azure.com",
            deployment="gpt-4o",
            credential=fake_cred,
        )
        return client, fake_cred

    def test_chat_complete_calls_openai(self) -> None:
        client, _ = self._make_client()
        fake_response = MagicMock()
        fake_response.choices[0].message.content = "Azure says hi"
        mock_openai = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = fake_response

        with patch.dict("sys.modules", {"openai": MagicMock(AzureOpenAI=mock_openai)}):
            result = client.chat_complete([{"role": "user", "content": "test"}])

        assert result == "Azure says hi"

    def test_as_callable(self) -> None:
        client, _ = self._make_client()
        fake_response = MagicMock()
        fake_response.choices[0].message.content = "hello"
        mock_openai = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = fake_response

        with patch.dict("sys.modules", {"openai": MagicMock(AzureOpenAI=mock_openai)}):
            fn = client.as_callable()
            result = fn([{"role": "user", "content": "ping"}])

        assert result == "hello"

    def test_missing_openai_raises(self) -> None:
        client, _ = self._make_client()
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(RuntimeError, match="openai"):
                client.chat_complete([{"role": "user", "content": "test"}])

    def test_temperature_clamped(self) -> None:
        client = AzureOpenAILLMClient(credential=MagicMock(), temperature=2.5)
        assert client._temperature == 1.0

    def test_chat_complete_passes_extra_headers_from_runtime_trace_context(self) -> None:
        client, _ = self._make_client()
        fake_response = MagicMock()
        fake_response.choices[0].message.content = "Azure says hi"
        mock_openai = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = fake_response

        with (
            patch.dict("sys.modules", {"openai": MagicMock(AzureOpenAI=mock_openai)}),
            scoped_trace_context(
                correlation_id="corr-llm-1",
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            ),
        ):
            result = client.chat_complete([{"role": "user", "content": "test"}])

        assert result == "Azure says hi"
        called_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
        assert called_kwargs["extra_headers"]["x-correlation-id"] == "corr-llm-1"
        assert (
            called_kwargs["extra_headers"]["traceparent"]
            == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )


# ---------------------------------------------------------------------------
# OllamaLLMClient
# ---------------------------------------------------------------------------


class TestOllamaLLMClient:
    def test_echo_when_ollama_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = OllamaLLMClient(base_url="http://localhost:11434", model="llama3")
        with patch(
            "runtime.assessment_orchestration.ollama_client.is_ollama_available",
            return_value=False,
        ):
            result = client.chat_complete([{"role": "user", "content": "hello?"}])
        assert "echo" in result.lower() or "hello?" in result

    def test_as_callable(self) -> None:
        client = OllamaLLMClient()
        with patch.object(client, "chat_complete", return_value="ollama reply") as mock_fn:
            fn = client.as_callable()
            result = fn([{"role": "user", "content": "hi"}])
        assert result == "ollama reply"

    def test_env_var_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "mistral")
        client = OllamaLLMClient()
        assert client._base_url == "http://custom:11434"
        assert client._model == "mistral"

    def test_echo_returns_last_user_message(self) -> None:
        client = OllamaLLMClient()
        result = client._echo(
            [
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "What is 2+2?"},
            ]
        )
        assert "What is 2+2?" in result

    def test_echo_no_user_message(self) -> None:
        client = OllamaLLMClient()
        result = client._echo([{"role": "system", "content": "sys"}])
        assert "no user message" in result.lower()
