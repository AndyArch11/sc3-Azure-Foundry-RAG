"""Shared environment-to-config resolution helpers for cloud providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, TypeVar

from .types import CloudProvider, normalise_cloud_provider

_E = TypeVar("_E", bound=Exception)


@dataclass(frozen=True)
class ProviderResolvedSettings:
    """Common provider-derived configuration used across services."""

    cloud_provider: CloudProvider
    is_aws: bool
    is_local: bool
    search_endpoint: str
    search_index_name: str
    controls_index_name: str
    openai_endpoint: str
    embedding_deployment: str
    query_deployment: str


@dataclass(frozen=True)
class QueryWebProviderSettings:
    """Query-web-specific provider-derived settings."""

    evaluator_deployment: str
    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_container_name: str
    cosmos_orchestration_container_name: str


def _required(
    values: Mapping[str, str],
    key: str,
    *,
    error_type: type[_E],
) -> str:
    value = (values.get(key) or "").strip()
    if not value:
        raise error_type(f"Missing required environment variable: {key}")
    return value


def parse_framework_authority_order(
    raw_value: str | None,
    *,
    default_order: tuple[str, ...],
    resolve_name: Callable[[str, str], str | None],
    drop_unknown: bool,
) -> tuple[str, ...]:
    """Parse framework order while preserving caller-specific alias behaviour."""

    if raw_value is None or not raw_value.strip():
        return default_order

    ordered: list[str] = []
    seen: set[str] = set()
    for part in raw_value.split(","):
        raw_item = part.strip()
        if not raw_item:
            continue
        resolved = resolve_name(raw_item.lower(), raw_item)
        if resolved is None:
            if drop_unknown:
                continue
            resolved = raw_item
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)

    return tuple(ordered or default_order)


def resolve_provider_settings(
    values: Mapping[str, str],
    *,
    missing_error: type[Exception],
    local_search_endpoint: str,
    local_openai_endpoint: str,
    local_openai_uses_default: bool,
) -> ProviderResolvedSettings:
    """Resolve common cloud-provider settings from environment mapping."""

    provider = normalise_cloud_provider(values.get("CLOUD_PROVIDER"))
    is_aws = provider == "aws"
    is_local = provider == "local"

    if is_local:
        search_endpoint = (values.get("AZURE_SEARCH_ENDPOINT") or local_search_endpoint).strip()
    elif is_aws:
        search_endpoint = _required(values, "OPENSEARCH_ENDPOINT", error_type=missing_error)
    else:
        search_endpoint = _required(values, "AZURE_SEARCH_ENDPOINT", error_type=missing_error)

    if is_aws:
        search_index_name = (
            values.get("SEARCH_INDEX_NAME") or values.get("OPENSEARCH_INDEX") or "grounding-index"
        ).strip()
        controls_index_name = (values.get("CONTROLS_INDEX_NAME") or "controls-index").strip()
        openai_endpoint = ""
        embedding_deployment = (values.get("BEDROCK_EMBEDDING_MODEL_ID") or "").strip()
        query_deployment = (values.get("BEDROCK_MODEL_ID") or "").strip()
    else:
        search_index_name = (values.get("AZURE_SEARCH_INDEX_NAME") or "grounding-index").strip()
        controls_index_name = (
            values.get("AZURE_SEARCH_CONTROLS_INDEX_NAME") or "controls-index"
        ).strip()
        if is_local and local_openai_uses_default:
            openai_endpoint = (values.get("AZURE_OPENAI_ENDPOINT") or local_openai_endpoint).strip()
        else:
            openai_endpoint = _required(values, "AZURE_OPENAI_ENDPOINT", error_type=missing_error)
        embedding_deployment = (
            values.get("EMBEDDING_DEPLOYMENT_NAME") or "text-embedding-ada-002"
        ).strip()
        query_deployment = (values.get("QUERY_DEPLOYMENT_NAME") or "gpt-5.1-chat").strip()

    return ProviderResolvedSettings(
        cloud_provider=provider,
        is_aws=is_aws,
        is_local=is_local,
        search_endpoint=search_endpoint,
        search_index_name=search_index_name,
        controls_index_name=controls_index_name,
        openai_endpoint=openai_endpoint,
        embedding_deployment=embedding_deployment,
        query_deployment=query_deployment,
    )


def resolve_query_web_provider_settings(
    values: Mapping[str, str],
    *,
    common: ProviderResolvedSettings,
    missing_error: type[Exception],
) -> QueryWebProviderSettings:
    """Resolve query-web provider-specific settings from environment mapping."""

    if common.is_aws:
        evaluator_deployment = (values.get("BEDROCK_MODEL_ID") or "").strip()
        cosmos_endpoint = ""
        cosmos_database_name = (values.get("AZURE_COSMOS_DATABASE_NAME") or "").strip()
        cosmos_container_name = (values.get("AZURE_COSMOS_CONTAINER_NAME") or "").strip()
    else:
        evaluator_deployment = (values.get("EVALUATOR_DEPLOYMENT_NAME") or "gpt-4.1-mini").strip()
        if common.is_local:
            cosmos_endpoint = (values.get("AZURE_COSMOS_ENDPOINT") or "").strip()
            cosmos_database_name = (
                values.get("AZURE_COSMOS_DATABASE_NAME") or "local-db"
            ).strip()
            cosmos_container_name = (
                values.get("AZURE_COSMOS_CONTAINER_NAME") or "local-conversations"
            ).strip()
        else:
            cosmos_endpoint = _required(
                values,
                "AZURE_COSMOS_ENDPOINT",
                error_type=missing_error,
            )
            cosmos_database_name = _required(
                values,
                "AZURE_COSMOS_DATABASE_NAME",
                error_type=missing_error,
            )
            cosmos_container_name = _required(
                values,
                "AZURE_COSMOS_CONTAINER_NAME",
                error_type=missing_error,
            )

    cosmos_orchestration_container_name = (
        values.get("AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME")
        or values.get("AZURE_COSMOS_CONTAINER_NAME")
        or "orchestration-state"
    ).strip()

    return QueryWebProviderSettings(
        evaluator_deployment=evaluator_deployment,
        cosmos_endpoint=cosmos_endpoint,
        cosmos_database_name=cosmos_database_name,
        cosmos_container_name=cosmos_container_name,
        cosmos_orchestration_container_name=cosmos_orchestration_container_name,
    )
