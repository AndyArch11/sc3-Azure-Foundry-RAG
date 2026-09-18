from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from query_web.models import AskResponse


def _load_openapi_contract() -> dict[str, Any]:
    with Path("docs/contracts/rag-api-v1.openapi.yaml").open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    assert isinstance(payload, dict)
    return payload


def _operation_exists(paths: dict[str, Any], path: str, method: str) -> bool:
    node = paths.get(path)
    return isinstance(node, dict) and isinstance(node.get(method.lower()), dict)


def test_openapi_contract_includes_only_external_graph_query_paths_and_methods() -> None:
    payload = _load_openapi_contract()
    paths = payload.get("paths")
    assert isinstance(paths, dict)

    assert not _operation_exists(paths, "/api/v1/graph/build", "post")
    assert _operation_exists(paths, "/api/v1/provider-status", "get")
    assert _operation_exists(paths, "/api/v1/graph/nodes/{node_id}", "get")
    assert _operation_exists(paths, "/api/v1/graph/related", "get")
    assert _operation_exists(paths, "/api/v1/graph/export", "get")
    assert _operation_exists(paths, "/api/v1/graph/status", "get")
    assert _operation_exists(paths, "/api/v1/frameworks/{framework}/controls", "get")


def test_openapi_contract_ask_response_graph_fields_are_optional() -> None:
    payload = _load_openapi_contract()
    schemas = ((payload.get("components") or {}).get("schemas")) or {}
    assert isinstance(schemas, dict)

    ask_response = schemas.get("AskResponse")
    assert isinstance(ask_response, dict)

    properties = ask_response.get("properties")
    assert isinstance(properties, dict)

    required = ask_response.get("required") or []
    assert isinstance(required, list)

    for field_name in [
        "graph_capabilities",
        "graph_summary",
        "corpus_a_entities",
        "corpus_b_entities",
        "graph_links",
    ]:
        field_schema = properties.get(field_name)
        assert isinstance(field_schema, dict), f"AskResponse.{field_name} missing in contract"
        assert field_schema.get("nullable") is True, f"AskResponse.{field_name} must be nullable"
        assert field_name not in required, f"AskResponse.{field_name} must remain optional"


def test_ask_response_model_graph_fields_remain_optional() -> None:
    for field_name in [
        "graph_capabilities",
        "graph_summary",
        "corpus_a_entities",
        "corpus_b_entities",
        "graph_links",
    ]:
        field = AskResponse.model_fields.get(field_name)
        assert field is not None, f"AskResponse.{field_name} missing in model"
        assert field.is_required() is False, f"AskResponse.{field_name} must remain optional"
        assert field.default is None, f"AskResponse.{field_name} default should be None"
