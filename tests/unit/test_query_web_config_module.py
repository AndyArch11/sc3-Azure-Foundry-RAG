"""Unit tests for query_web/config.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import query_web.config as config_module
from query_web.config import (
    _CANONICAL_FRAMEWORKS,
    _FRAMEWORK_ALIASES,
    PrecedencePolicy,
    QueryConfig,
    _canonical_framework_name,
    _env_bool,
    _form_bool,
    _load_precedence_policy,
    _parse_framework_authority_order,
    _require_env,
    _thinking_defaults,
    _thinking_mode_presets_for_ui,
    load_config,
)

# ---------------------------------------------------------------------------
# _canonical_framework_name
# ---------------------------------------------------------------------------


def test_canonical_framework_name_none_input() -> None:
    assert _canonical_framework_name(None) is None


def test_canonical_framework_name_empty_string() -> None:
    assert _canonical_framework_name("") is None
    assert _canonical_framework_name("   ") is None


def test_canonical_framework_name_known_alias() -> None:
    assert _canonical_framework_name("nist_ai_rmf") == "NIST AI RMF"
    assert _canonical_framework_name("ai rmf") == "NIST AI RMF"
    assert _canonical_framework_name("nist") == "NIST CSF"
    assert _canonical_framework_name("e8") == "Essential Eight"
    assert _canonical_framework_name("ism") == "ISM"
    assert _canonical_framework_name("pci_dss") == "PCI DSS"
    assert _canonical_framework_name("pspf") == "PSPF"
    assert _canonical_framework_name("cis_controls") == "CIS Controls"
    assert _canonical_framework_name("aescsf") == "AESCSF"


def test_canonical_framework_name_exact_canonical() -> None:
    for name in _CANONICAL_FRAMEWORKS:
        assert _canonical_framework_name(name) == name


def test_canonical_framework_name_unknown_value() -> None:
    assert _canonical_framework_name("HIPAA") is None
    assert _canonical_framework_name("some-random-framework") is None


def test_canonical_framework_name_case_insensitive_alias() -> None:
    assert _canonical_framework_name("NIST CSF") == "NIST CSF"
    assert _canonical_framework_name("Essential Eight") == "Essential Eight"


# ---------------------------------------------------------------------------
# _require_env
# ---------------------------------------------------------------------------


def test_require_env_raises_when_missing() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("__TEST_MISSING_VAR__", None)
        with pytest.raises(RuntimeError, match="__TEST_MISSING_VAR__"):
            _require_env("__TEST_MISSING_VAR__")


def test_require_env_raises_when_empty() -> None:
    with patch.dict(os.environ, {"__TEST_EMPTY_VAR__": ""}):
        with pytest.raises(RuntimeError, match="__TEST_EMPTY_VAR__"):
            _require_env("__TEST_EMPTY_VAR__")


def test_require_env_returns_value() -> None:
    with patch.dict(os.environ, {"__TEST_VAR__": "myvalue"}):
        assert _require_env("__TEST_VAR__") == "myvalue"


# ---------------------------------------------------------------------------
# _env_bool
# ---------------------------------------------------------------------------


def test_env_bool_returns_default_when_not_set() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("__TEST_BOOL__", None)
        assert _env_bool("__TEST_BOOL__", default=True) is True
        assert _env_bool("__TEST_BOOL__", default=False) is False


def test_env_bool_truthy_values() -> None:
    for val in ("1", "true", "True", "TRUE", "yes", "YES", "on", "ON"):
        with patch.dict(os.environ, {"__TEST_BOOL__": val}):
            assert _env_bool("__TEST_BOOL__") is True


def test_env_bool_falsy_values() -> None:
    for val in ("0", "false", "no", "off", ""):
        with patch.dict(os.environ, {"__TEST_BOOL__": val}):
            assert _env_bool("__TEST_BOOL__") is False


# ---------------------------------------------------------------------------
# _form_bool
# ---------------------------------------------------------------------------


def test_form_bool_none_returns_default() -> None:
    assert _form_bool(None, default=True) is True
    assert _form_bool(None, default=False) is False


def test_form_bool_empty_string_returns_default() -> None:
    assert _form_bool("", default=True) is True
    assert _form_bool("   ", default=False) is False


def test_form_bool_truthy_values() -> None:
    for val in ("1", "true", "True", "yes", "YES", "on"):
        assert _form_bool(val) is True


def test_form_bool_falsy_values() -> None:
    for val in ("0", "false", "no", "off", "random"):
        assert _form_bool(val) is False


# ---------------------------------------------------------------------------
# _parse_framework_authority_order
# ---------------------------------------------------------------------------


def test_parse_framework_authority_order_none_returns_default() -> None:
    result = _parse_framework_authority_order(None)
    assert result[0] == "Essential Eight"
    assert "ISM" in result
    assert "NIST AI RMF" in result
    assert "NIST CSF" in result
    assert len(result) == 8


def test_parse_framework_authority_order_empty_string_returns_default() -> None:
    assert _parse_framework_authority_order("   ") == _parse_framework_authority_order(None)


def test_parse_framework_authority_order_parses_aliases() -> None:
    result = _parse_framework_authority_order("nist,e8,ism")
    assert result == ("NIST CSF", "Essential Eight", "ISM")


def test_parse_framework_authority_order_deduplicates() -> None:
    result = _parse_framework_authority_order("nist,nist csf,ism")
    assert result.count("NIST CSF") == 1


def test_parse_framework_authority_order_skips_unknown() -> None:
    result = _parse_framework_authority_order("nist,HIPAA,ism")
    assert "HIPAA" not in result
    assert "NIST CSF" in result
    assert "ISM" in result


def test_parse_framework_authority_order_all_unknown_falls_back_to_default() -> None:
    default = _parse_framework_authority_order(None)
    result = _parse_framework_authority_order("HIPAA,SOC2,ISO27001")
    assert result == default


# ---------------------------------------------------------------------------
# _load_precedence_policy
# ---------------------------------------------------------------------------

_FALLBACK: tuple[str, ...] = ("Essential Eight", "ISM", "NIST CSF")


def test_load_precedence_policy_empty_path_returns_default() -> None:
    policy = _load_precedence_policy("", _FALLBACK)
    assert policy.version == "v1-default"
    assert policy.default_framework_order == _FALLBACK
    assert policy.rules == tuple()


def test_load_precedence_policy_missing_file_returns_default(tmp_path: Path) -> None:
    policy = _load_precedence_policy(str(tmp_path / "nonexistent.json"), _FALLBACK)
    assert policy.version == "v1-default"
    assert policy.default_framework_order == _FALLBACK


def test_load_precedence_policy_invalid_json_returns_default(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not-json{{{", encoding="utf-8")
    policy = _load_precedence_policy(str(bad_file), _FALLBACK)
    assert policy.version == "v1-default"


def test_load_precedence_policy_valid_file(tmp_path: Path) -> None:
    data = {
        "version": "v2",
        "default_framework_order": ["ISM", "NIST CSF", "Essential Eight"],
        "rules": [
            {
                "rule_id": "r1",
                "applies_when_keywords": ["backup"],
                "preferred_framework": "Essential Eight",
            }
        ],
    }
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert policy.version == "v2"
    assert policy.default_framework_order == ("ISM", "NIST CSF", "Essential Eight")
    assert len(policy.rules) == 1
    assert policy.rules[0]["rule_id"] == "r1"


def test_load_precedence_policy_skips_non_string_order_items(tmp_path: Path) -> None:
    data = {
        "version": "v1",
        "default_framework_order": ["ISM", 42, None, "NIST CSF"],
        "rules": [],
    }
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert policy.default_framework_order == ("ISM", "NIST CSF")


def test_load_precedence_policy_skips_non_dict_rules(tmp_path: Path) -> None:
    data = {
        "version": "v1",
        "default_framework_order": ["ISM"],
        "rules": [{"rule_id": "r1"}, "not-a-dict", 42],
    }
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert len(policy.rules) == 1


def test_load_precedence_policy_empty_order_falls_back(tmp_path: Path) -> None:
    data = {"version": "v1", "default_framework_order": [], "rules": []}
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert policy.default_framework_order == _FALLBACK


def test_load_precedence_policy_unknown_order_items_fall_back(tmp_path: Path) -> None:
    data = {"version": "v1", "default_framework_order": ["HIPAA", "SOC2"], "rules": []}
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert policy.default_framework_order == _FALLBACK


def test_load_precedence_policy_no_version_key_defaults_to_v1(tmp_path: Path) -> None:
    data = {"default_framework_order": ["ISM"], "rules": []}
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert policy.version == "v1"


def test_load_precedence_policy_rules_not_list_returns_empty(tmp_path: Path) -> None:
    data = {"version": "v1", "default_framework_order": ["ISM"], "rules": "not-a-list"}
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    policy = _load_precedence_policy(str(policy_file), _FALLBACK)
    assert policy.rules == tuple()


# ---------------------------------------------------------------------------
# load_config (smoke test via env vars)
# ---------------------------------------------------------------------------

_REQUIRED_ENVS = {
    "CLOUD_PROVIDER": "azure",
    "AZURE_SEARCH_ENDPOINT": "https://search.example.com",
    "AZURE_OPENAI_ENDPOINT": "https://openai.example.com",
    "AZURE_COSMOS_ENDPOINT": "https://cosmos.example.com",
    "AZURE_COSMOS_DATABASE_NAME": "mydb",
    "AZURE_COSMOS_CONTAINER_NAME": "mycontainer",
}


def test_load_config_returns_query_config() -> None:
    with patch.dict(os.environ, _REQUIRED_ENVS):
        cfg = load_config()
    assert isinstance(cfg, QueryConfig)
    assert cfg.cloud_provider == "azure"
    assert cfg.search_endpoint == "https://search.example.com"
    assert cfg.openai_endpoint == "https://openai.example.com"


def test_load_config_applies_env_overrides() -> None:
    env = {**_REQUIRED_ENVS, "SEARCH_TOP_K": "10", "CONTROLS_TOP_K": "8"}
    with patch.dict(os.environ, env):
        cfg = load_config()
    assert cfg.search_top_k == 10
    assert cfg.controls_top_k == 8


def test_load_config_deep_thinking_mode_applies_defaults() -> None:
    env = {**_REQUIRED_ENVS, "THINKING_MODE": "deep"}
    with patch.dict(os.environ, env):
        cfg = load_config()
    assert cfg.search_top_k == 8
    assert cfg.controls_top_k == 6
    assert cfg.default_temperature == 0.2
    assert cfg.evaluator_temperature == 0.1
    assert cfg.top_p == 0.85
    assert cfg.max_completion_tokens >= 2200


def test_thinking_defaults_progress_monotonically() -> None:
    quick = _thinking_defaults(
        mode="quick",
        default_max_completion_tokens=1400,
        default_evaluator_max_completion_tokens=800,
    )
    balanced = _thinking_defaults(
        mode="balanced",
        default_max_completion_tokens=1400,
        default_evaluator_max_completion_tokens=800,
    )
    deep = _thinking_defaults(
        mode="deep",
        default_max_completion_tokens=1400,
        default_evaluator_max_completion_tokens=800,
    )

    assert (
        quick["default_temperature"] < balanced["default_temperature"] < deep["default_temperature"]
    )
    assert (
        quick["evaluator_temperature"]
        <= balanced["evaluator_temperature"]
        < deep["evaluator_temperature"]
    )
    assert quick["top_p"] > balanced["top_p"] > deep["top_p"]


def test_thinking_mode_presets_for_ui_include_evaluator_fields() -> None:
    presets = _thinking_mode_presets_for_ui(
        default_max_completion_tokens=1400,
        default_evaluator_max_completion_tokens=800,
    )

    assert presets["quick"]["evaluator_temperature"] == 0.05
    assert presets["balanced"]["evaluator_temperature"] == 0.075
    assert presets["deep"]["evaluator_temperature"] == 0.1
    assert presets["quick"]["evaluation_threshold"] == 0.70
    assert presets["balanced"]["evaluation_threshold"] == 0.72
    assert presets["deep"]["evaluation_threshold"] == 0.78


def test_load_config_thinking_mode_keeps_explicit_overrides() -> None:
    env = {
        **_REQUIRED_ENVS,
        "THINKING_MODE": "quick",
        "SEARCH_TOP_K": "11",
        "MAX_COMPLETION_TOKENS": "3000",
    }
    with patch.dict(os.environ, env):
        cfg = load_config()
    assert cfg.search_top_k == 11
    assert cfg.max_completion_tokens == 3000


def test_load_config_raises_on_missing_required_env() -> None:
    env = {k: v for k, v in _REQUIRED_ENVS.items() if k != "AZURE_SEARCH_ENDPOINT"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="AZURE_SEARCH_ENDPOINT"):
            load_config()


def test_load_config_local_uses_resource_adaptive_token_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module, "_detect_host_resources", lambda: (6.0, 2))
    with patch.dict(os.environ, {"CLOUD_PROVIDER": "local"}, clear=True):
        cfg = load_config()
    assert cfg.max_completion_tokens == 512
    assert cfg.evaluator_max_completion_tokens == 256


def test_load_config_local_allows_env_token_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module, "_detect_host_resources", lambda: (6.0, 2))
    with patch.dict(
        os.environ,
        {
            "CLOUD_PROVIDER": "local",
            "MAX_COMPLETION_TOKENS": "1700",
            "EVALUATOR_MAX_COMPLETION_TOKENS": "900",
        },
        clear=True,
    ):
        cfg = load_config()
    assert cfg.max_completion_tokens == 1700
    assert cfg.evaluator_max_completion_tokens == 900


def test_load_config_aws_uses_aws_envs_and_skips_azure_requirements() -> None:
    with patch.dict(
        os.environ,
        {
            "CLOUD_PROVIDER": "aws",
            "OPENSEARCH_ENDPOINT": "https://search-aws.example.com",
            "SEARCH_INDEX_NAME": "grounding-index",
            "CONTROLS_INDEX_NAME": "controls-index",
            "BEDROCK_MODEL_ID": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        },
        clear=True,
    ):
        cfg = load_config()

    assert cfg.cloud_provider == "aws"
    assert cfg.search_endpoint == "https://search-aws.example.com"
    assert cfg.search_index_name == "grounding-index"
    assert cfg.controls_index_name == "controls-index"
    assert cfg.openai_endpoint == ""
    assert cfg.query_deployment == "anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert cfg.evaluator_deployment == "anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert cfg.cosmos_endpoint == ""


def test_load_config_aws_raises_when_opensearch_endpoint_missing() -> None:
    with patch.dict(os.environ, {"CLOUD_PROVIDER": "aws"}, clear=True):
        with pytest.raises(RuntimeError, match="OPENSEARCH_ENDPOINT"):
            load_config()
