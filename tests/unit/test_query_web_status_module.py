from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from query_web.endpoints.status import register_status_endpoints


class _DummySearchClient:
    def search(self, query_text: str, top: int = 1):
        return [{"id": "x"}]


def _make_config(**overrides):
    defaults = dict(
        cloud_provider="local",
        search_index_name="grounding-index",
        controls_index_name="controls-index",
        controls_semantic_default=True,
        controls_framework_authority_order=("NIST CSF", "ISM"),
        precedence_policy_path="/tmp/policy.json",
        prompt_injection_validator_enabled=False,
        prompt_injection_validator_mode="off",
        prompt_injection_validator_temperature=0.5,
        required_group_object_id="",
        auth_token="",
        embedding_deployment="embed",
        query_deployment="query",
        evaluator_deployment="eval",
        search_top_k=5,
        controls_top_k=4,
        controls_semantic_configuration_name="controls-semantic",
        ingestion_job_name="",
        default_temperature=0.1,
        evaluator_temperature=0.1,
        evaluation_threshold=0.7,
        prompt_injection_validator_threshold=0.85,
        max_completion_tokens=1400,
        evaluator_max_completion_tokens=800,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _register_app(config, resolve_query_model_capabilities=None) -> TestClient:
    app = FastAPI()
    register_status_endpoints(
        app,
        config,
        _DummySearchClient(),
        _DummySearchClient(),
        QUERY_WEB_VERSION_SIGNATURE="test-version",
        precedence_policy=SimpleNamespace(
            version="v1", default_framework_order=("NIST CSF",), rules=[]
        ),
        _CONTROLS_FRAMEWORK_FILTERS={"nist_csf": "NIST CSF"},
        _CORPUS_A_FRAMEWORKS={"nist_csf": {}},
        _is_corpus_upload_enabled=lambda: True,
        _is_ingestion_job_trigger_enabled=lambda: False,
        COMPLIANCE_REPORT_SCHEMA_VERSION="v1",
        resolve_query_model_capabilities=resolve_query_model_capabilities,
    )
    return TestClient(app)


def test_provider_status_returns_runtime_config_hints_without_probe() -> None:
    client = _register_app(
        _make_config(max_completion_tokens=1600, evaluator_max_completion_tokens=900)
    )

    response = client.get("/api/provider-status")
    assert response.status_code == 200
    payload = response.json()

    assert payload["provider"] == "local"
    hints = payload["runtime_hints"]
    assert hints["max_completion_tokens_effective"] == 1600
    assert hints["evaluator_max_completion_tokens_effective"] == 900
    assert hints["max_completion_tokens_source"] == "runtime_config"


def test_provider_status_applies_model_capability_caps_when_available() -> None:
    client = _register_app(
        _make_config(max_completion_tokens=1600, evaluator_max_completion_tokens=900),
        resolve_query_model_capabilities=lambda: {
            "source": "model_metadata",
            "context_window_tokens": 65536,
            "max_output_tokens": 700,
            "models": {
                "chat-model": {"context_window_tokens": 65536},
                "embed-model": {"context_window_tokens": 8192},
            },
        },
    )

    response = client.get("/api/provider-status")
    assert response.status_code == 200
    payload = response.json()

    hints = payload["runtime_hints"]
    assert hints["context_window_tokens_hint"] == 65536
    assert hints["context_window_source"] == "model_metadata"
    assert hints["max_completion_tokens_effective"] == 700
    assert hints["evaluator_max_completion_tokens_effective"] == 700
    assert hints["max_completion_tokens_source"] == "model_metadata"
    assert hints["model_capabilities"]["embed-model"]["context_window_tokens"] == 8192
