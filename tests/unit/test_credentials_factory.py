"""Unit tests for the runtime credential provider factory."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from runtime.credentials import get_credential_provider
from runtime.credentials.aws_cred import AWSCredentialProvider
from runtime.credentials.azure_cred import AzureCredentialProvider
from runtime.credentials.local_cred import LocalCredential, LocalCredentialProvider


class TestCredentialFactoryDispatch:
    """Tests factory selects the right provider."""

    def test_default_is_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
        with patch("runtime.credentials.azure_cred.DefaultAzureCredential"):
            provider = get_credential_provider()
        assert isinstance(provider, AzureCredentialProvider)

    def test_explicit_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        with patch("runtime.credentials.azure_cred.DefaultAzureCredential"):
            provider = get_credential_provider()
        assert isinstance(provider, AzureCredentialProvider)

    def test_explicit_azure_uppercase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "AZURE")
        with patch("runtime.credentials.azure_cred.DefaultAzureCredential"):
            provider = get_credential_provider()
        assert isinstance(provider, AzureCredentialProvider)

    def test_explicit_aws(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        provider = get_credential_provider()
        assert isinstance(provider, AWSCredentialProvider)

    def test_explicit_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        provider = get_credential_provider()
        assert isinstance(provider, LocalCredentialProvider)

    def test_explicit_dev_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "dev")
        provider = get_credential_provider()
        assert isinstance(provider, LocalCredentialProvider)

    def test_argument_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "azure")
        provider = get_credential_provider(cloud_provider="local")
        assert isinstance(provider, LocalCredentialProvider)

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        with pytest.raises(ValueError, match="Unsupported cloud provider"):
            get_credential_provider()

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "  local  ")
        provider = get_credential_provider()
        assert isinstance(provider, LocalCredentialProvider)


class TestAzureCredentialProvider:
    """Tests Azure provider adapter."""

    def test_provider_name(self) -> None:
        with patch("runtime.credentials.azure_cred.DefaultAzureCredential"):
            provider = AzureCredentialProvider()
        assert provider.get_provider_name() == "azure"

    def test_get_sdk_credential_returns_azure_credential(self) -> None:
        mock_cred = MagicMock()
        with patch(
            "runtime.credentials.azure_cred.DefaultAzureCredential", return_value=mock_cred
        ):
            provider = AzureCredentialProvider()
        assert provider.get_sdk_credential() is mock_cred


class TestAWSCredentialProvider:
    """Tests AWS provider adapter."""

    def test_provider_name(self) -> None:
        provider = AWSCredentialProvider()
        assert provider.get_provider_name() == "aws"

    def test_get_sdk_credential_returns_session(self) -> None:
        mock_session = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.Session.return_value = mock_session
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = AWSCredentialProvider(profile_name="test", region_name="us-east-1")
            result = provider.get_sdk_credential()
        mock_boto3.Session.assert_called_once_with(profile_name="test", region_name="us-east-1")
        assert result is mock_session

    def test_missing_boto3_raises(self) -> None:
        with patch.dict("sys.modules", {"boto3": None}):
            provider = AWSCredentialProvider()
            with pytest.raises(RuntimeError, match="boto3 is required"):
                provider.get_sdk_credential()

    def test_defaults_none(self) -> None:
        mock_boto3 = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = AWSCredentialProvider()
            provider.get_sdk_credential()
        mock_boto3.Session.assert_called_once_with(profile_name=None, region_name=None)

    def test_env_vars_passed_to_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_PROFILE", "my-profile")
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        mock_boto3 = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            provider = get_credential_provider(cloud_provider="aws")
            provider.get_sdk_credential()
        mock_boto3.Session.assert_called_once_with(
            profile_name="my-profile", region_name="ap-southeast-2"
        )


class TestLocalCredentialProvider:
    """Tests local provider adapter."""

    def test_provider_name(self) -> None:
        provider = LocalCredentialProvider()
        assert provider.get_provider_name() == "local"

    def test_get_sdk_credential_returns_local_credential(self) -> None:
        provider = LocalCredentialProvider()
        cred = provider.get_sdk_credential()
        assert isinstance(cred, LocalCredential)
        assert cred.provider == "local"

    def test_credential_is_immutable(self) -> None:
        cred = LocalCredential()
        with pytest.raises((AttributeError, TypeError)):
            cred.provider = "other"  # type: ignore[misc]
