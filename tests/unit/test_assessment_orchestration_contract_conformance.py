from __future__ import annotations

from pathlib import Path

import yaml

from runtime.assessment_orchestration.mcp.confluence import ConfluenceMCPServer
from runtime.assessment_orchestration.mcp.email import EmailMCPServer
from runtime.assessment_orchestration.mcp.sharepoint import SharePointMCPServer
from runtime.assessment_orchestration.schema_validation import assert_schema_value


def _load_yaml(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    assert isinstance(payload, dict)
    return payload


def _extract_tool_names(payload: dict) -> list[str]:
    tools = payload.get("tools") or []
    assert isinstance(tools, list)
    names: list[str] = []
    for tool in tools:
        assert isinstance(tool, dict)
        name = tool.get("tool_name")
        assert isinstance(name, str) and name
        names.append(name)
    return names


def _iter_tools(payload: dict) -> list[dict]:
    tools = payload.get("tools") or []
    assert isinstance(tools, list)
    for tool in tools:
        assert isinstance(tool, dict)
    return tools


def _iter_event_types(payload: dict) -> list[dict]:
    event_types = payload.get("event_types") or []
    assert isinstance(event_types, list)
    for event_type in event_types:
        assert isinstance(event_type, dict)
    return event_types


def test_sharepoint_contract_tools_have_stub_methods() -> None:
    payload = _load_yaml("docs/contracts/mcp-sharepoint-tools.yaml")
    assert payload["contract_type"] == "mcp_tool_set"
    assert payload["provider"] == "sharepoint"
    tool_names = _extract_tool_names(payload)
    cls = SharePointMCPServer

    for name in tool_names:
        assert hasattr(cls, name), f"SharePointMCPServer missing method for contract tool {name}"


def test_confluence_contract_tools_have_stub_methods() -> None:
    payload = _load_yaml("docs/contracts/mcp-confluence-tools.yaml")
    assert payload["contract_type"] == "mcp_tool_set"
    assert payload["provider"] == "confluence"
    tool_names = _extract_tool_names(payload)
    cls = ConfluenceMCPServer

    for name in tool_names:
        assert hasattr(cls, name), f"ConfluenceMCPServer missing method for contract tool {name}"


def test_email_contract_tools_have_stub_methods() -> None:
    payload = _load_yaml("docs/contracts/mcp-email-tools.yaml")
    assert payload["contract_type"] == "mcp_tool_set"
    assert payload["provider"] == "email"
    tool_names = _extract_tool_names(payload)
    cls = EmailMCPServer

    for name in tool_names:
        assert hasattr(cls, name), f"EmailMCPServer missing method for contract tool {name}"


def test_mcp_tool_contract_examples_match_declared_schemas() -> None:
    for contract_path in [
        "docs/contracts/mcp-sharepoint-tools.yaml",
        "docs/contracts/mcp-confluence-tools.yaml",
        "docs/contracts/mcp-email-tools.yaml",
    ]:
        payload = _load_yaml(contract_path)

        for tool in _iter_tools(payload):
            tool_name = tool.get("tool_name")
            assert isinstance(tool_name, str) and tool_name

            input_schema = tool.get("input_schema")
            output_schema = tool.get("output_schema")
            example_input = tool.get("example_input")
            example_output = tool.get("example_output")

            assert isinstance(
                input_schema, dict
            ), f"{contract_path} {tool_name} missing input_schema"
            assert isinstance(
                output_schema, dict
            ), f"{contract_path} {tool_name} missing output_schema"
            assert example_input is not None, f"{contract_path} {tool_name} missing example_input"
            assert example_output is not None, f"{contract_path} {tool_name} missing example_output"

            assert_schema_value(payload, input_schema, example_input)
            assert_schema_value(payload, output_schema, example_output)


def test_shared_schema_mentions_core_objects() -> None:
    payload = _load_yaml("docs/contracts/shared-schemas.yaml")
    assert payload["contract_type"] == "shared_schemas"
    schemas = payload.get("schemas")
    assert isinstance(schemas, dict)
    for schema_name in [
        "assessment_job",
        "resolved_target",
        "assessed_artifact_package",
        "corpus_grounding_package",
        "structured_assessment_report",
        "delivery_plan",
        "delivery_outcome",
    ]:
        assert schema_name in schemas


def test_queue_contract_mentions_message_types() -> None:
    payload = _load_yaml("docs/contracts/orchestrator-queue-message.yaml")
    assert payload["contract_type"] == "orchestrator_queue_message"
    message_types = payload.get("message_types") or []
    assert isinstance(message_types, list)
    values = {
        item["message_type"]
        for item in message_types
        if isinstance(item, dict) and "message_type" in item
    }
    assert "assessment_requested" in values
    assert "assessment_retry_requested" in values


def test_provider_event_contract_examples_match_required_fields_and_normalisation() -> None:
    for contract_path in [
        "docs/contracts/provider-events-sharepoint.yaml",
        "docs/contracts/provider-events-confluence.yaml",
        "docs/contracts/provider-events-email.yaml",
    ]:
        payload = _load_yaml(contract_path)
        provider = payload.get("provider")
        assert isinstance(provider, str) and provider

        for event_type in _iter_event_types(payload):
            event_name = event_type.get("event_type")
            assert isinstance(event_name, str) and event_name

            required_fields = event_type.get("required_fields") or []
            normalised_output = event_type.get("normalised_output") or {}
            example_event = event_type.get("example_event") or {}

            assert isinstance(
                required_fields, list
            ), f"{contract_path} {event_name} invalid required_fields"
            assert isinstance(
                normalised_output, dict
            ), f"{contract_path} {event_name} invalid normalised_output"
            assert (
                isinstance(example_event, dict) and example_event
            ), f"{contract_path} {event_name} missing example_event"

            for field_name in required_fields:
                assert (
                    field_name in example_event
                ), f"{contract_path} {event_name} example missing required field {field_name}"

            assert "provider" in normalised_output
            assert "trigger_type" in normalised_output
            assert "source_type" in normalised_output
            assert normalised_output["provider"] == provider
