"""Application configuration: dataclasses, env-var helpers, and the loader."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.provider_core import (
    parse_framework_authority_order,
    resolve_provider_settings,
    resolve_query_web_provider_settings,
)

logger = logging.getLogger(__name__)

_THINKING_MODE_ALIASES: dict[str, str] = {
    "": "balanced",
    "balanced": "balanced",
    "normal": "balanced",
    "default": "balanced",
    "quick": "quick",
    "fast": "quick",
    "low": "quick",
    "deep": "deep",
    "thorough": "deep",
    "high": "deep",
}


# ---------------------------------------------------------------------------
# Framework name normalisation helpers
# ---------------------------------------------------------------------------

_FRAMEWORK_ALIASES: dict[str, str] = {
    "nist ai rmf": "NIST AI RMF",
    "nist ai risk management framework": "NIST AI RMF",
    "ai rmf": "NIST AI RMF",
    "airmf": "NIST AI RMF",
    "nist_ai_rmf": "NIST AI RMF",
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
    "NIST AI RMF",
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


def _detect_host_resources() -> tuple[float, int]:
    """Best-effort host resource detection (RAM GiB, CPU cores)."""
    cpu_count = os.cpu_count() or 1

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        if (
            isinstance(page_size, int)
            and isinstance(page_count, int)
            and page_size > 0
            and page_count > 0
        ):
            ram_bytes = float(page_size * page_count)
            return (ram_bytes / (1024**3), cpu_count)
    except (AttributeError, OSError, ValueError):
        pass

    # Linux fallback when sysconf does not provide usable values.
    try:
        with Path("/proc/meminfo").open("r", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        ram_kib = int(parts[1])
                        return (ram_kib / (1024**2), cpu_count)
    except OSError:
        pass

    return (8.0, cpu_count)


def _local_completion_token_defaults() -> tuple[int, int]:
    """Adaptive local defaults for query/evaluator max completion token caps."""
    ram_gib, cpu_count = _detect_host_resources()

    if ram_gib < 8 or cpu_count <= 4:
        return (512, 256)
    if ram_gib < 16 or cpu_count <= 8:
        return (900, 512)
    if ram_gib < 32:
        return (1400, 800)
    if ram_gib < 64:
        return (2200, 1000)
    return (3200, 1400)


def _normalise_thinking_mode(raw: str | None) -> str:
    mode = (raw or "").strip().lower()
    resolved = _THINKING_MODE_ALIASES.get(mode)
    if resolved is not None:
        return resolved
    logger.warning("Unknown THINKING_MODE '%s'; falling back to 'balanced'", raw)
    return "balanced"


def _thinking_defaults(
    *,
    mode: str,
    default_max_completion_tokens: int,
    default_evaluator_max_completion_tokens: int,
) -> dict[str, float | int]:
    if mode == "quick":
        return {
            "search_top_k": 3,
            "controls_top_k": 3,
            "default_temperature": 0.1,
            "evaluator_temperature": 0.1,
            "evaluation_threshold": 0.70,
            "max_completion_tokens": min(default_max_completion_tokens, 900),
            "evaluator_max_completion_tokens": min(default_evaluator_max_completion_tokens, 512),
        }

    if mode == "deep":
        return {
            "search_top_k": 8,
            "controls_top_k": 6,
            "default_temperature": 0.2,
            "evaluator_temperature": 0.15,
            "evaluation_threshold": 0.78,
            "max_completion_tokens": max(default_max_completion_tokens, 2200),
            "evaluator_max_completion_tokens": max(default_evaluator_max_completion_tokens, 1000),
        }

    return {
        "search_top_k": 5,
        "controls_top_k": 4,
        "default_temperature": 1.0,
        "evaluator_temperature": 1.0,
        "evaluation_threshold": 0.72,
        "max_completion_tokens": default_max_completion_tokens,
        "evaluator_max_completion_tokens": default_evaluator_max_completion_tokens,
    }


def _thinking_mode_presets_for_ui(
    *,
    default_max_completion_tokens: int,
    default_evaluator_max_completion_tokens: int,
) -> dict[str, dict[str, float | int]]:
    presets: dict[str, dict[str, float | int]] = {}
    for mode in ("quick", "balanced", "deep"):
        defaults = _thinking_defaults(
            mode=mode,
            default_max_completion_tokens=default_max_completion_tokens,
            default_evaluator_max_completion_tokens=default_evaluator_max_completion_tokens,
        )
        presets[mode] = {
            "retrieve_k": int(defaults["search_top_k"]),
            "controls_top_k": int(defaults["controls_top_k"]),
            "temperature": float(defaults["default_temperature"]),
            "max_completion_tokens": int(defaults["max_completion_tokens"]),
            "evaluator_max_completion_tokens": int(
                defaults["evaluator_max_completion_tokens"]
            ),
        }
    return presets


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryConfig:
    """Runtime configuration for query-web endpoints and helpers."""

    cloud_provider: str
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
    s3_bucket_name: str

    ingestion_job_subscription_id: str
    ingestion_job_resource_group: str
    ingestion_job_name: str

    ecs_cluster_name: str
    ingestion_task_definition_arn: str
    ecs_sg_id: str
    ecs_subnet_id: str

    default_temperature: float
    evaluator_temperature: float
    evaluation_threshold: float
    max_completion_tokens: int
    evaluator_max_completion_tokens: int
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
    default_framework: str = ""


# ---------------------------------------------------------------------------
# Precedence policy loader
# ---------------------------------------------------------------------------


def _parse_framework_authority_order(raw_value: str | None) -> tuple[str, ...]:
    """Parse framework authority ordering from env into canonical framework names."""
    default_order = (
        "Essential Eight",
        "ISM",
        "AESCSF",
        "NIST AI RMF",
        "NIST CSF",
        "PSPF",
        "PCI DSS",
        "CIS Controls",
    )
    return parse_framework_authority_order(
        raw_value,
        default_order=default_order,
        resolve_name=lambda normalised, raw: _canonical_framework_name(normalised),
        drop_unknown=True,
    )


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

    default_framework_raw = payload.get("default_framework", "")
    default_framework: str = ""
    if isinstance(default_framework_raw, str) and default_framework_raw.strip():
        default_framework = (
            _canonical_framework_name(default_framework_raw.strip())
            or default_framework_raw.strip()
        )
    if not default_framework and order:
        default_framework = order[0]

    return PrecedencePolicy(
        version=version,
        default_framework_order=order,
        rules=tuple(rules),
        default_framework=default_framework,
    )


# ---------------------------------------------------------------------------
# Main config loader
# ---------------------------------------------------------------------------


def load_config() -> QueryConfig:
    """Load and normalise application configuration from environment variables."""

    values = dict(os.environ)
    common = resolve_provider_settings(
        values,
        missing_error=RuntimeError,
        local_search_endpoint="http://local-search",
        local_openai_endpoint="http://local-llm",
        local_openai_uses_default=True,
    )
    query_web_provider = resolve_query_web_provider_settings(
        values,
        common=common,
        missing_error=RuntimeError,
    )
    provider = common.cloud_provider
    is_aws = common.is_aws
    is_local = common.is_local
    if is_local:
        local_max_completion_tokens, local_evaluator_max_completion_tokens = (
            _local_completion_token_defaults()
        )
    else:
        local_max_completion_tokens, local_evaluator_max_completion_tokens = (4096, 1400)

    thinking_mode = _normalise_thinking_mode(os.getenv("THINKING_MODE"))
    defaults = _thinking_defaults(
        mode=thinking_mode,
        default_max_completion_tokens=local_max_completion_tokens,
        default_evaluator_max_completion_tokens=local_evaluator_max_completion_tokens,
    )

    return QueryConfig(
        cloud_provider=provider,
        search_endpoint=common.search_endpoint,
        search_index_name=common.search_index_name,
        controls_index_name=common.controls_index_name,
        openai_endpoint=common.openai_endpoint,
        embedding_deployment=common.embedding_deployment,
        query_deployment=common.query_deployment,
        evaluator_deployment=query_web_provider.evaluator_deployment,
        search_top_k=int(os.getenv("SEARCH_TOP_K", str(defaults["search_top_k"]))),
        controls_top_k=int(os.getenv("CONTROLS_TOP_K", str(defaults["controls_top_k"]))),
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
        s3_bucket_name=os.getenv("S3_BUCKET_NAME", "").strip(),
        ingestion_job_subscription_id=os.getenv("INGESTION_JOB_SUBSCRIPTION_ID", "").strip(),
        ingestion_job_resource_group=os.getenv("INGESTION_JOB_RESOURCE_GROUP", "").strip(),
        ingestion_job_name=os.getenv("INGESTION_JOB_NAME", "").strip(),
        ecs_cluster_name=os.getenv("ECS_CLUSTER_NAME", "").strip(),
        ingestion_task_definition_arn=os.getenv("INGESTION_TASK_DEFINITION_ARN", "").strip(),
        ecs_sg_id=os.getenv("ECS_SG_ID", "").strip(),
        ecs_subnet_id=os.getenv("ECS_SUBNET_ID", "").strip(),
        default_temperature=float(
            os.getenv("DEFAULT_TEMPERATURE", str(defaults["default_temperature"]))
        ),
        evaluator_temperature=float(
            os.getenv("EVALUATOR_TEMPERATURE", str(defaults["evaluator_temperature"]))
        ),
        evaluation_threshold=float(
            os.getenv("ACCEPTABLE_SCORE_THRESHOLD", str(defaults["evaluation_threshold"]))
        ),
        max_completion_tokens=max(
            256,
            int(os.getenv("MAX_COMPLETION_TOKENS", str(defaults["max_completion_tokens"]))),
        ),
        evaluator_max_completion_tokens=max(
            128,
            int(
                os.getenv(
                    "EVALUATOR_MAX_COMPLETION_TOKENS",
                    str(defaults["evaluator_max_completion_tokens"]),
                )
            ),
        ),
        auth_token=os.getenv("QUERY_WEB_AUTH_TOKEN", "").strip(),
        required_group_object_id=os.getenv("QUERY_WEB_REQUIRED_GROUP_OBJECT_ID", "").strip(),
        cosmos_endpoint=query_web_provider.cosmos_endpoint,
        cosmos_database_name=query_web_provider.cosmos_database_name,
        cosmos_container_name=query_web_provider.cosmos_container_name,
        cosmos_orchestration_container_name=query_web_provider.cosmos_orchestration_container_name,
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
