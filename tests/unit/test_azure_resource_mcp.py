from __future__ import annotations

from typing import Any

import pytest

from runtime.assessment_orchestration.mcp.azure_resource import (
    AzureMCPServer,
    build_azure_target_reference,
)


class _FakeCredential:
    class _Token:
        token = "fake-token"

    def get_token(self, scope: str):
        assert scope == "https://management.azure.com/.default"
        return self._Token()


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_build_reference_and_resolve_resource_group_scope() -> None:
    target_reference = build_azure_target_reference(
        subscription_id="sub-1",
        resource_group="rg-1",
        resource_ids=[],
    )
    server = AzureMCPServer(
        credential=_FakeCredential(), http_get=lambda *args, **kwargs: _FakeResponse({"value": []})
    )

    target = server.resolve_target(target_reference)

    assert target.provider == "azure"
    assert target.target_type == "resource_scope"
    assert target.metadata["subscription_id"] == "sub-1"
    assert target.metadata["resource_group"] == "rg-1"
    assert target.metadata["scope_mode"] == "resource_group"


def test_get_content_by_id_extracts_resource_list() -> None:
    requests_seen: list[str] = []

    def _fake_http_get(url: str, **kwargs) -> _FakeResponse:
        requests_seen.append(url)
        if "/resourceGroups/rg-1/resources" in url:
            return _FakeResponse(
                {
                    "value": [
                        {
                            "id": "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/st1",
                            "name": "st1",
                            "type": "Microsoft.Storage/storageAccounts",
                            "location": "australiaeast",
                            "tags": {"env": "test"},
                        }
                    ]
                }
            )
        if "/providers/Microsoft.Authorization/policyAssignments" in url:
            return _FakeResponse(
                {
                    "value": [
                        {
                            "id": "/subscriptions/sub-1/providers/Microsoft.Authorization/policyAssignments/pa-1",
                            "name": "pa-1",
                            "type": "Microsoft.Authorization/policyAssignments",
                            "properties": {
                                "scope": "/subscriptions/sub-1/resourceGroups/rg-1",
                                "displayName": "Require tags",
                                "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/pd-1",
                                "enforcementMode": "Default",
                                "parameters": {"tagName": {"value": "env"}},
                            },
                        }
                    ]
                }
            )
        if "/providers/Microsoft.Authorization/policyDefinitions/pd-1" in url:
            return _FakeResponse(
                {
                    "id": "/providers/Microsoft.Authorization/policyDefinitions/pd-1",
                    "name": "pd-1",
                    "type": "Microsoft.Authorization/policyDefinitions",
                    "properties": {
                        "displayName": "Require environment tag",
                        "policyType": "BuiltIn",
                        "mode": "All",
                        "parameters": {"tagName": {"type": "String"}},
                        "policyRule": {"then": {"effect": "deny"}},
                    },
                }
            )
        return _FakeResponse({"value": []})

    server = AzureMCPServer(credential=_FakeCredential(), http_get=_fake_http_get)
    target = server.resolve_target(
        build_azure_target_reference(
            subscription_id="sub-1",
            resource_group="rg-1",
            resource_ids=[],
        )
    )

    artifact = server.get_content_by_id(target.target_id, identity_mode="app_only")

    assert artifact.provider == "azure"
    assert artifact.metadata["resource_count"] == 1
    assert artifact.metadata["policy_assignment_count"] == 1
    assert artifact.metadata["policy_definition_count"] == 1
    assert "Microsoft.Storage/storageAccounts" in artifact.content
    assert "policy_assignments" in artifact.content
    assert "Require environment tag" in artifact.content
    assert requests_seen


def test_resolve_target_rejects_mixed_scope_resource_ids() -> None:
    server = AzureMCPServer(
        credential=_FakeCredential(), http_get=lambda *args, **kwargs: _FakeResponse({})
    )

    with pytest.raises(ValueError, match="same subscription and resource group"):
        server.resolve_target(
            build_azure_target_reference(
                subscription_id="sub-1",
                resource_group="rg-1",
                resource_ids=[
                    "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Compute/virtualMachines/vm1",
                    "/subscriptions/sub-1/resourceGroups/rg-2/providers/Microsoft.Compute/virtualMachines/vm2",
                ],
            )
        )
