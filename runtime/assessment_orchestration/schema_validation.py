from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import yaml


class SchemaValidationError(ValueError):
    pass


def load_yaml_contract(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise SchemaValidationError("Contract payload must be a YAML object")
    return payload


def to_plain_data(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def resolve_schema_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/schemas/"):
        raise SchemaValidationError(f"Unsupported schema ref: {ref}")
    schema_name = ref.split("/", 2)[-1]
    schemas = root.get("schemas")
    if not isinstance(schemas, dict):
        raise SchemaValidationError("Root payload does not include schemas object")
    resolved = schemas.get(schema_name)
    if not isinstance(resolved, dict):
        raise SchemaValidationError(f"Referenced schema not found: {schema_name}")
    return resolved


def assert_schema_value(root: dict[str, Any], schema: dict[str, Any], value: Any) -> None:
    has_value_constraint = False

    if "$ref" in schema:
        resolved = resolve_schema_ref(root, str(schema["$ref"]))
        assert_schema_value(root, resolved, value)
        return

    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"Expected constant value {schema['const']!r}, got {value!r}")
    if "const" in schema:
        has_value_constraint = True

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"Expected value in {schema['enum']!r}, got {value!r}")
    if "enum" in schema:
        has_value_constraint = True

    schema_type = schema.get("type")
    if schema_type is None and has_value_constraint:
        return

    if schema_type == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(f"Expected string, got {type(value).__name__}")
        return

    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise SchemaValidationError(f"Expected boolean, got {type(value).__name__}")
        return

    if schema_type == "array":
        if not isinstance(value, (list, tuple)):
            raise SchemaValidationError(f"Expected array, got {type(value).__name__}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                assert_schema_value(root, item_schema, item)
        return

    if schema_type == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(f"Expected object, got {type(value).__name__}")

        required = schema.get("required") or []
        for name in required:
            if name not in value:
                raise SchemaValidationError(f"Missing required field: {name}")

        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            raise SchemaValidationError("Schema properties must be an object")

        for name, prop_schema in properties.items():
            if name in value and isinstance(prop_schema, dict):
                assert_schema_value(root, prop_schema, value[name])

        if schema.get("additionalProperties") is False:
            allowed = set(properties.keys())
            actual = set(value.keys())
            unexpected = sorted(actual - allowed)
            if unexpected:
                raise SchemaValidationError(f"Unexpected fields: {unexpected}")
        return

    raise SchemaValidationError(f"Unsupported schema type: {schema_type}")


def assert_named_schema(root: dict[str, Any], schema_name: str, value: Any) -> None:
    schemas = root.get("schemas")
    if not isinstance(schemas, dict):
        raise SchemaValidationError("Root payload does not include schemas object")
    schema = schemas.get(schema_name)
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"Schema not found: {schema_name}")
    assert_schema_value(root, schema, to_plain_data(value))
