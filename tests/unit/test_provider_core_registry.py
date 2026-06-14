"""Unit tests for runtime.provider_core registry scaffolding."""

from __future__ import annotations

import pytest

from runtime.provider_core import (
    DEFAULT_CLOUD_PROVIDER_REGISTRY,
    CloudProviderRegistry,
    build_default_registry,
    normalise_cloud_provider,
)


class TestNormaliseCloudProvider:
    def test_defaults_to_azure(self) -> None:
        assert normalise_cloud_provider(None) == "azure"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("azure", "azure"),
            ("AWS", "aws"),
            (" local ", "local"),
            ("dev", "local"),
        ],
    )
    def test_normalises_aliases_and_case(self, raw: str, expected: str) -> None:
        assert normalise_cloud_provider(raw) == expected

    def test_rejects_unknown_provider(self) -> None:
        with pytest.raises(ValueError, match="Unsupported cloud provider"):
            normalise_cloud_provider("gcp")


class TestCloudProviderRegistry:
    def test_default_registry_includes_builtin_providers(self) -> None:
        registry = build_default_registry()
        assert registry.providers() == ("aws", "azure", "local")

    def test_get_accepts_dev_alias(self) -> None:
        adapter = DEFAULT_CLOUD_PROVIDER_REGISTRY.get("dev")
        assert adapter.provider == "local"

    def test_duplicate_registration_raises(self) -> None:
        registry = build_default_registry()
        with pytest.raises(ValueError, match="already registered"):
            registry.register(DEFAULT_CLOUD_PROVIDER_REGISTRY.get("aws"))

    def test_unregistered_provider_raises(self) -> None:
        registry = CloudProviderRegistry()
        with pytest.raises(ValueError, match="is not registered"):
            registry.get("azure")


class TestBuiltinAdapterMapping:
    def test_azure_mapping_uses_azure_keywords(self) -> None:
        azure = DEFAULT_CLOUD_PROVIDER_REGISTRY.get("azure")
        result = azure.map_search_request(
            query_text="security",
            filter_expr="framework eq 'nist'",
            top=5,
            select=["id", "title"],
            include_total_count=True,
        )
        assert result == {
            "search_text": "security",
            "filter": "framework eq 'nist'",
            "top": 5,
            "include_total_count": True,
            "select": ["id", "title"],
        }

    def test_aws_mapping_uses_opensearch_keywords(self) -> None:
        aws = DEFAULT_CLOUD_PROVIDER_REGISTRY.get("aws")
        result = aws.map_search_request(
            query_text="security",
            filter_expr="",
            top=3,
            select=None,
            include_total_count=True,
        )
        assert result == {
            "query_text": "security",
            "filters": None,
            "top": 3,
            "select": None,
        }

    def test_local_mapping_uses_neutral_keywords(self) -> None:
        local = DEFAULT_CLOUD_PROVIDER_REGISTRY.get("local")
        result = local.map_search_request(
            query_text="security",
            filter_expr="category eq 'control'",
            top=8,
            select=["id"],
        )
        assert result == {
            "query_text": "security",
            "filters": "category eq 'control'",
            "top": 8,
            "select": ["id"],
        }


class TestBuiltinCapabilities:
    def test_azure_capabilities(self) -> None:
        caps = DEFAULT_CLOUD_PROVIDER_REGISTRY.get("azure").capabilities
        assert caps.supports_embeddings is True
        assert caps.supports_semantic_search is True

    def test_aws_capabilities(self) -> None:
        caps = DEFAULT_CLOUD_PROVIDER_REGISTRY.get("aws").capabilities
        assert caps.supports_embeddings is True
        assert caps.supports_semantic_search is True
        assert caps.supports_background_assessment_worker is True
