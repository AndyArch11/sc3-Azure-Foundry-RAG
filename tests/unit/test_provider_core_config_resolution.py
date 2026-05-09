"""Unit tests for shared provider config resolution helpers."""

from __future__ import annotations

import pytest

from runtime.provider_core import (
    parse_framework_authority_order,
    resolve_provider_settings,
    resolve_query_web_provider_settings,
)


def test_resolve_provider_settings_azure() -> None:
    cfg = resolve_provider_settings(
        {
            "CLOUD_PROVIDER": "azure",
            "AZURE_SEARCH_ENDPOINT": "https://search.example.com",
            "AZURE_OPENAI_ENDPOINT": "https://openai.example.com",
            "AZURE_SEARCH_INDEX_NAME": "grounding-index",
            "AZURE_SEARCH_CONTROLS_INDEX_NAME": "controls-index",
            "EMBEDDING_DEPLOYMENT_NAME": "embed-deploy",
            "QUERY_DEPLOYMENT_NAME": "query-deploy",
        },
        missing_error=RuntimeError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )

    assert cfg.cloud_provider == "azure"
    assert cfg.search_endpoint == "https://search.example.com"
    assert cfg.openai_endpoint == "https://openai.example.com"
    assert cfg.search_index_name == "grounding-index"
    assert cfg.controls_index_name == "controls-index"
    assert cfg.embedding_deployment == "embed-deploy"
    assert cfg.query_deployment == "query-deploy"


def test_resolve_provider_settings_aws() -> None:
    cfg = resolve_provider_settings(
        {
            "CLOUD_PROVIDER": "aws",
            "OPENSEARCH_ENDPOINT": "https://search-aws.example.com",
            "SEARCH_INDEX_NAME": "grounding-index",
            "CONTROLS_INDEX_NAME": "controls-index",
            "BEDROCK_EMBEDDING_MODEL_ID": "embed-model",
            "BEDROCK_MODEL_ID": "chat-model",
        },
        missing_error=ValueError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )

    assert cfg.cloud_provider == "aws"
    assert cfg.is_aws is True
    assert cfg.search_endpoint == "https://search-aws.example.com"
    assert cfg.openai_endpoint == ""
    assert cfg.embedding_deployment == "embed-model"
    assert cfg.query_deployment == "chat-model"


def test_resolve_provider_settings_local_uses_defaults_for_query_web() -> None:
    cfg = resolve_provider_settings(
        {"CLOUD_PROVIDER": "dev"},
        missing_error=RuntimeError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )

    assert cfg.cloud_provider == "local"
    assert cfg.is_local is True
    assert cfg.search_endpoint == "http://local-search"
    assert cfg.openai_endpoint == "http://local-llm"


def test_resolve_provider_settings_local_requires_openai_for_runtime() -> None:
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        resolve_provider_settings(
            {"CLOUD_PROVIDER": "local", "AZURE_SEARCH_ENDPOINT": "https://search"},
            missing_error=ValueError,
            local_search_endpoint="http://local-search",
            local_openai_endpoint="",
            local_openai_uses_default=False,
        )


def test_resolve_provider_settings_raises_with_requested_exception_type() -> None:
    with pytest.raises(RuntimeError, match="AZURE_SEARCH_ENDPOINT"):
        resolve_provider_settings(
            {"CLOUD_PROVIDER": "azure", "AZURE_OPENAI_ENDPOINT": "https://openai"},
            missing_error=RuntimeError,
            local_search_endpoint="http://local-search",
            local_openai_endpoint="http://local-llm",
            local_openai_uses_default=True,
        )


def test_resolve_query_web_provider_settings_azure_requires_cosmos_values() -> None:
    common = resolve_provider_settings(
        {
            "CLOUD_PROVIDER": "azure",
            "AZURE_SEARCH_ENDPOINT": "https://search.example.com",
            "AZURE_OPENAI_ENDPOINT": "https://openai.example.com",
        },
        missing_error=RuntimeError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )

    with pytest.raises(RuntimeError, match="AZURE_COSMOS_ENDPOINT"):
        resolve_query_web_provider_settings(
            {
                "CLOUD_PROVIDER": "azure",
                "AZURE_SEARCH_ENDPOINT": "https://search.example.com",
                "AZURE_OPENAI_ENDPOINT": "https://openai.example.com",
            },
            common=common,
            missing_error=RuntimeError,
        )


def test_resolve_query_web_provider_settings_aws_cosmos_optional() -> None:
    common = resolve_provider_settings(
        {
            "CLOUD_PROVIDER": "aws",
            "OPENSEARCH_ENDPOINT": "https://search-aws.example.com",
            "BEDROCK_MODEL_ID": "chat-model",
        },
        missing_error=ValueError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )

    cfg = resolve_query_web_provider_settings(
        {
            "CLOUD_PROVIDER": "aws",
            "BEDROCK_MODEL_ID": "chat-model",
        },
        common=common,
        missing_error=ValueError,
    )

    assert cfg.evaluator_deployment == "chat-model"
    assert cfg.cosmos_endpoint == ""
    assert cfg.cosmos_database_name == ""
    assert cfg.cosmos_container_name == ""
    assert cfg.cosmos_orchestration_container_name == "orchestration-state"


def test_resolve_query_web_provider_settings_local_defaults() -> None:
    common = resolve_provider_settings(
        {"CLOUD_PROVIDER": "local"},
        missing_error=RuntimeError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )

    cfg = resolve_query_web_provider_settings(
        {"CLOUD_PROVIDER": "local"},
        common=common,
        missing_error=RuntimeError,
    )

    assert cfg.evaluator_deployment == "gpt-4.1-mini"
    assert cfg.cosmos_endpoint == ""
    assert cfg.cosmos_database_name == "local-db"
    assert cfg.cosmos_container_name == "local-conversations"
    assert cfg.cosmos_orchestration_container_name == "orchestration-state"


def test_parse_framework_authority_order_drop_unknown() -> None:
    parsed = parse_framework_authority_order(
        "nist,unknown,ism,nist",
        default_order=("Essential Eight",),
        resolve_name=lambda normalised, raw: {
            "nist": "NIST CSF",
            "ism": "ISM",
        }.get(normalised),
        drop_unknown=True,
    )
    assert parsed == ("NIST CSF", "ISM")


def test_parse_framework_authority_order_preserve_unknown() -> None:
    parsed = parse_framework_authority_order(
        "nist,HIPAA,ism,HIPAA",
        default_order=("Essential Eight",),
        resolve_name=lambda normalised, raw: {
            "nist": "NIST CSF",
            "ism": "ISM",
        }.get(normalised),
        drop_unknown=False,
    )
    assert parsed == ("NIST CSF", "HIPAA", "ISM")
