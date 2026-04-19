"""Application configuration: dataclasses, env-var helpers, and the loader."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Framework name normalisation helpers
# ---------------------------------------------------------------------------

_FRAMEWORK_ALIASES: dict[str, str] = {
    "nist": "NIST CSF",
    "nist csf": "NIST CSF",
    "csf": "NIST CSF",
    "csf 2": "NIST CSF",
    "csf 2.0": "NIST CSF",
    "nist_csf": "NIST CSF",
    "essential eight": "Essential Eight",
    "essential_eight": "Essential Eight",
    "e8": "Essential Eight",
    "aescsf": "AESCSF",
    "cis": "CIS Controls",
    "cis controls": "CIS Controls",
    "cis_controls": "CIS Controls",
    "ism": "ISM",
    "information security manual": "ISM",
    "pci": "PCI DSS",
    "pci dss": "PCI DSS",
    "pci-dss": "PCI DSS",
    "pci_dss": "PCI DSS",
    "pci dss v4": "PCI DSS",
    "pspf": "PSPF",
    "protective security policy framework": "PSPF",
}

_CANONICAL_FRAMEWORKS: set[str] = {
    "NIST CSF",
    "Essential Eight",
    "AESCSF",
    "CIS Controls",
    "ISM",
    "PCI DSS",
    "PSPF",
}


def _canonical_framework_name(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = raw_value.strip().lower()
    if not value:
        return None
    candidate = _FRAMEWORK_ALIASES.get(value, raw_value.strip())
    return candidate if candidate in _CANONICAL_FRAMEWORKS else None


# ---------------------------------------------------------------------------
# Environment / form helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable not set: {name}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _form_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryConfig:
    """Runtime configuration for query-web endpoints and helpers."""

    search_endpoint: str
    search_index_name: str
    controls_index_name: str
    openai_endpoint: str
    embedding_deployment: str
    query_deployment: str
    evaluator_deployment: str
    search_top_k: int
    controls_top_k: int
    controls_semantic_default: bool
    controls_semantic_configuration_name: str
    controls_framework_authority_order: tuple[str, ...]
    precedence_policy_path: str

    storage_account_name: str
    storage_container_name: str

    ingestion_job_subscription_id: str
    ingestion_job_resource_group: str
    ingestion_job_name: str

    default_temperature: float
    evaluator_temperature: float
    evaluation_threshold: float
    auth_token: str
    required_group_object_id: str

    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_container_name: str
    cosmos_orchestration_container_name: str

    prompt_injection_validator_enabled: bool
    prompt_injection_validator_deployment: str
    prompt_injection_validator_threshold: float
    prompt_injection_validator_temperature: float
    prompt_injection_validator_timeout_s: int
    prompt_injection_validator_mode: str
    guardrail_metrics_in_response: bool

    branding_static_path: str
    app_title: str


@dataclass(frozen=True)
class PrecedencePolicy:
    """Framework precedence policy used for control conflict resolution."""

    version: str
    default_framework_order: tuple[str, ...]
    rules: tuple[dict[str, Any], ...]


# ---------------------------------------------------------------------------
# Precedence policy loader
# ---------------------------------------------------------------------------


def _parse_framework_authority_order(raw_value: str | None) -> tuple[str, ...]:
    """Parse framework authority ordering from env into canonical framework names."""
    default_order = (
        "Essential Eight",
        "ISM",
        "AESCSF",
        "NIST CSF",
        "PSPF",
        "PCI DSS",
        "CIS Controls",
    )
    if raw_value is None or not raw_value.strip():
        return default_order

    ordered: list[str] = []
    seen: set[str] = set()
    parts = [part.strip().lower() for part in raw_value.split(",") if part.strip()]
    for part in parts:
        name = _canonical_framework_name(part)
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)

    if not ordered:
        return default_order

    return tuple(ordered)


def _load_precedence_policy(
    policy_path: str,
    fallback_order: tuple[str, ...],
) -> PrecedencePolicy:
    """Load precedence policy JSON; fall back safely if file is missing/invalid."""
    default_policy = PrecedencePolicy(
        version="v1-default",
        default_framework_order=fallback_order,
        rules=tuple(),
    )

    if not policy_path:
        return default_policy

    path = Path(policy_path)
    if not path.exists():
        logger.warning("Precedence policy file not found: %s", policy_path)
        return default_policy

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse precedence policy file %s: %s", policy_path, exc)
        return default_policy

    version = str(payload.get("version", "v1")).strip() or "v1"
    order_raw = payload.get("default_framework_order")
    order: tuple[str, ...] = fallback_order
    if isinstance(order_raw, list):
        normalised: list[str] = []
        seen: set[str] = set()
        for item in order_raw:
            if not isinstance(item, str):
                continue
            name = _canonical_framework_name(item)
            if name and name not in seen:
                seen.add(name)
                normalised.append(name)
        if normalised:
            order = tuple(normalised)

    rules_raw = payload.get("rules")
    rules: list[dict[str, Any]] = []
    if isinstance(rules_raw, list):
        for item in rules_raw:
            if isinstance(item, dict):
                rules.append(item)

    return PrecedencePolicy(version=version, default_framework_order=order, rules=tuple(rules))


# ---------------------------------------------------------------------------
# Main config loader
# ---------------------------------------------------------------------------


def load_config() -> QueryConfig:
    """Load and normalise application configuration from environment variables."""

    return QueryConfig(
        search_endpoint=_require_env("AZURE_SEARCH_ENDPOINT"),
        search_index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "grounding-index"),
        controls_index_name=os.getenv("AZURE_SEARCH_CONTROLS_INDEX_NAME", "controls-index"),
        openai_endpoint=_require_env("AZURE_OPENAI_ENDPOINT"),
        embedding_deployment=os.getenv("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-ada-002"),
        query_deployment=os.getenv("QUERY_DEPLOYMENT_NAME", "gpt-5.1-chat"),
        evaluator_deployment=os.getenv("EVALUATOR_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        search_top_k=int(os.getenv("SEARCH_TOP_K", "5")),
        controls_top_k=int(os.getenv("CONTROLS_TOP_K", "4")),
        controls_semantic_default=_env_bool("CONTROLS_SEMANTIC_DEFAULT", default=False),
        controls_semantic_configuration_name=os.getenv(
            "AZURE_SEARCH_CONTROLS_SEMANTIC_CONFIG", "controls-semantic"
        ),
        controls_framework_authority_order=_parse_framework_authority_order(
            os.getenv("CONTROLS_FRAMEWORK_AUTHORITY_ORDER")
        ),
        precedence_policy_path=os.getenv(
            "PRECEDENCE_POLICY_PATH", "/app/policies/precedence_policy.json"
        ).strip(),
        storage_account_name=os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "").strip(),
        storage_container_name=os.getenv("AZURE_STORAGE_CONTAINER_NAME", "grounding-data").strip(),
        ingestion_job_subscription_id=os.getenv("INGESTION_JOB_SUBSCRIPTION_ID", "").strip(),
        ingestion_job_resource_group=os.getenv("INGESTION_JOB_RESOURCE_GROUP", "").strip(),
        ingestion_job_name=os.getenv("INGESTION_JOB_NAME", "").strip(),
        default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "1")),
        evaluator_temperature=float(os.getenv("EVALUATOR_TEMPERATURE", "1.0")),
        evaluation_threshold=float(os.getenv("ACCEPTABLE_SCORE_THRESHOLD", "0.72")),
        auth_token=os.getenv("QUERY_WEB_AUTH_TOKEN", "").strip(),
        required_group_object_id=os.getenv("QUERY_WEB_REQUIRED_GROUP_OBJECT_ID", "").strip(),
        cosmos_endpoint=_require_env("AZURE_COSMOS_ENDPOINT"),
        cosmos_database_name=_require_env("AZURE_COSMOS_DATABASE_NAME"),
        cosmos_container_name=_require_env("AZURE_COSMOS_CONTAINER_NAME"),
        cosmos_orchestration_container_name=os.getenv(
            "AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME",
            os.getenv("AZURE_COSMOS_CONTAINER_NAME", "orchestration-state"),
        ).strip(),
        prompt_injection_validator_enabled=_env_bool(
            "PROMPT_INJECTION_VALIDATOR_ENABLED", default=False
        ),
        prompt_injection_validator_deployment=os.getenv(
            "PROMPT_INJECTION_VALIDATOR_DEPLOYMENT",
            os.getenv("EVALUATOR_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        ),
        prompt_injection_validator_threshold=float(
            os.getenv("PROMPT_INJECTION_VALIDATOR_THRESHOLD", "0.85")
        ),
        prompt_injection_validator_temperature=float(
            os.getenv("PROMPT_INJECTION_VALIDATOR_TEMPERATURE", "0.5")
        ),
        prompt_injection_validator_timeout_s=int(
            os.getenv("PROMPT_INJECTION_VALIDATOR_TIMEOUT_S", "15")
        ),
        prompt_injection_validator_mode=os.getenv("PROMPT_INJECTION_VALIDATOR_MODE", "off").lower(),
        guardrail_metrics_in_response=_env_bool("GUARDRAIL_METRICS_IN_RESPONSE", default=False),
        branding_static_path=os.getenv("BRANDING_STATIC_PATH", "").strip(),
        app_title=os.getenv("APP_TITLE", "RAG Query Console").strip(),
    )
