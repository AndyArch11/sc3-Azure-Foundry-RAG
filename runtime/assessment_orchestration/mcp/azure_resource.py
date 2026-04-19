from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests  # type: ignore[import-untyped]
from azure.identity import DefaultAzureCredential

from ..models import AccessDecision, AssessedArtifactPackage, PersonReference, ResolvedTarget


def build_azure_target_reference(
    *,
    subscription_id: str,
    resource_group: str,
    resource_ids: list[str] | None = None,
) -> str:
    """Run build azure target reference."""
    encoded_sub = quote(subscription_id.strip(), safe="")
    encoded_rg = quote(resource_group.strip(), safe="")
    encoded_ids = ",".join(
        quote(item.strip(), safe="") for item in (resource_ids or []) if item.strip()
    )
    return f"azure://scope?subscription_id={encoded_sub}&resource_group={encoded_rg}&resource_ids={encoded_ids}"


class AzureMCPServer:
    """AzureMCPServer."""

    provider = "azure"

    def __init__(
        self,
        *,
        credential: DefaultAzureCredential | None = None,
        management_base_url: str = "https://management.azure.com",
        max_resources: int = 100,
        http_get: Callable[..., requests.Response] | None = None,
    ) -> None:
        """Run init."""
        self._credential = credential or DefaultAzureCredential()
        self._management_base_url = management_base_url.rstrip("/")
        self._resolved_scopes: dict[str, dict[str, Any]] = {}
        self._max_resources = max(1, max_resources)
        self._http_get = http_get or requests.get

    def resolve_target(
        self, target_reference: str, *, requester_context: dict[str, Any] | None = None
    ) -> ResolvedTarget:
        """Run resolve target."""
        scope = self._parse_target_reference(target_reference)
        canonical_scope = json.dumps(scope, sort_keys=True)
        target_id = hashlib.sha256(canonical_scope.encode("utf-8")).hexdigest()[:24]
        self._resolved_scopes[target_id] = scope

        resource_ids = list(scope.get("resource_ids") or [])
        title = (
            f"Azure resources ({len(resource_ids)})"
            if resource_ids
            else f"Azure resource group {scope['resource_group']}"
        )
        return ResolvedTarget(
            provider="azure",
            target_type="resource_scope",
            target_id=target_id,
            canonical_url=target_reference,
            title=title,
            container_id=scope["subscription_id"],
            metadata=scope,
        )

    def check_user_access(
        self, target_id: str, delegated_user_context: dict[str, Any]
    ) -> AccessDecision:
        """Run check user access."""
        return AccessDecision(
            granted=True,
            identity_mode="delegated",
            reason="azure_read_only_access_enforced_by_upstream_auth",
            audit_fields={"target_id": target_id},
        )

    def get_content_by_id(
        self,
        target_id: str,
        *,
        identity_mode: str,
        include_discussion_context: bool = False,
    ) -> AssessedArtifactPackage:
        """Run get content by id."""
        if identity_mode not in {"app_only", "delegated"}:
            raise ValueError("identity_mode must be app_only or delegated")

        scope = self._resolved_scopes.get(target_id)
        if scope is None:
            raise ValueError(f"Unknown Azure target_id: {target_id}")

        extraction = self._extract_configuration(scope)
        content = json.dumps(extraction, sort_keys=True, indent=2)
        title = extraction.get("title") or "Azure resource extract"
        return AssessedArtifactPackage(
            provider="azure",
            target_id=target_id,
            canonical_url=str(extraction.get("canonical_target") or ""),
            title=str(title),
            content=content,
            metadata={
                "identity_mode": identity_mode,
                "resource_count": extraction.get("resource_count", 0),
                "policy_assignment_count": extraction.get("policy_assignment_count", 0),
                "policy_definition_count": extraction.get("policy_definition_count", 0),
                "resource_scope": extraction.get("scope", {}),
                "version": extraction.get("extracted_at", ""),
                "assessment_evidence_scope": "azure_resource_configuration_and_policy_assignments",
                "framework_applicability_model": "azure_technical_control_prefilter_v1",
            },
            owner=None,
            last_editor=None,
            discussion_context=[],
        )

    def get_flagged_item_context(
        self,
        target_id: str,
        *,
        identity_mode: str,
        trigger_context: dict[str, Any] | None = None,
    ) -> AssessedArtifactPackage:
        """Run get flagged item context."""
        return self.get_content_by_id(
            target_id, identity_mode=identity_mode, include_discussion_context=False
        )

    def resolve_page_owner(self, target_id: str) -> dict[str, Any]:
        """Run resolve page owner."""
        return {"principal_id": "", "display_name": "", "email": ""}

    def resolve_last_editor(self, target_id: str) -> dict[str, Any]:
        """Run resolve last editor."""
        return {"principal_id": "", "display_name": "", "email": ""}

    def _parse_target_reference(self, target_reference: str) -> dict[str, Any]:
        """Run parse target reference."""
        value = target_reference.strip()
        if value.startswith("/subscriptions/"):
            return self._scope_from_resource_ids([value])

        parsed = urlparse(value)
        if parsed.scheme != "azure" or parsed.netloc != "scope":
            raise ValueError("Azure target reference must use azure://scope or a resource ID path")

        query = parse_qs(parsed.query)
        subscription_id = unquote(str((query.get("subscription_id") or [""])[0]).strip())
        resource_group = unquote(str((query.get("resource_group") or [""])[0]).strip())
        encoded_ids = str((query.get("resource_ids") or [""])[0])
        resource_ids = [unquote(item).strip() for item in encoded_ids.split(",") if item.strip()]

        if resource_ids:
            return self._scope_from_resource_ids(
                resource_ids, fallback_subscription=subscription_id
            )

        if not subscription_id or not resource_group:
            raise ValueError(
                "Azure scope requires subscription_id and resource_group when resource_ids are not supplied"
            )
        return {
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "resource_ids": [],
            "scope_mode": "resource_group",
        }

    def _scope_from_resource_ids(
        self,
        resource_ids: list[str],
        fallback_subscription: str = "",
    ) -> dict[str, Any]:
        """Run scope from resource ids."""
        normalised = [item.strip() for item in resource_ids if item.strip()]
        if not normalised:
            raise ValueError("At least one Azure resource ID is required")

        first_parts = [part for part in normalised[0].split("/") if part]
        if (
            len(first_parts) < 8
            or first_parts[0].lower() != "subscriptions"
            or first_parts[2].lower() != "resourcegroups"
        ):
            raise ValueError(f"Invalid Azure resource ID: {normalised[0]}")

        subscription_id = first_parts[1]
        resource_group = first_parts[3]
        for item in normalised[1:]:
            parts = [part for part in item.split("/") if part]
            if (
                len(parts) < 8
                or parts[0].lower() != "subscriptions"
                or parts[2].lower() != "resourcegroups"
            ):
                raise ValueError(f"Invalid Azure resource ID: {item}")
            if parts[1] != subscription_id or parts[3].lower() != resource_group.lower():
                raise ValueError(
                    "All resource_ids must belong to the same subscription and resource group"
                )

        if fallback_subscription and fallback_subscription != subscription_id:
            raise ValueError("resource_ids subscription does not match subscription_id")

        return {
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "resource_ids": normalised,
            "scope_mode": "resource_ids",
        }

    def _extract_configuration(self, scope: dict[str, Any]) -> dict[str, Any]:
        """Run extract configuration."""
        subscription_id = str(scope["subscription_id"])
        resource_group = str(scope["resource_group"])
        requested_ids = list(scope.get("resource_ids") or [])

        if requested_ids:
            resources = [self._read_resource_by_id(resource_id) for resource_id in requested_ids]
        else:
            resources = self._list_resources_in_group(subscription_id, resource_group)

        resources = resources[: self._max_resources]
        policy_context = self._extract_policy_context(
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_ids=requested_ids,
        )
        extracted_at = datetime.now(UTC).isoformat()
        canonical_target = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        title = f"Azure extract for {resource_group} ({len(resources)} resources)"
        return {
            "title": title,
            "canonical_target": canonical_target,
            "extracted_at": extracted_at,
            "scope": {
                "subscription_id": subscription_id,
                "resource_group": resource_group,
                "scope_mode": str(scope.get("scope_mode") or "resource_group"),
                "resource_ids": requested_ids,
            },
            "resource_count": len(resources),
            "resources": resources,
            "policy_scope_paths": policy_context["scope_paths"],
            "policy_assignment_count": policy_context["assignment_count"],
            "policy_assignments": policy_context["assignments"],
            "policy_definition_count": policy_context["referenced_definition_count"],
            "policy_definitions": policy_context["referenced_definitions"],
        }

    def _extract_policy_context(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        resource_ids: list[str],
    ) -> dict[str, Any]:
        """Run extract policy context."""
        scope_paths = [
            f"/subscriptions/{subscription_id}",
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}",
        ]
        scope_paths.extend(
            resource_id for resource_id in resource_ids if resource_id not in scope_paths
        )

        assignments_by_id: dict[str, dict[str, Any]] = {}
        for scope_path in scope_paths:
            for assignment in self._list_policy_assignments(scope_path):
                assignment_id = str(assignment.get("id") or "").strip()
                if assignment_id and assignment_id not in assignments_by_id:
                    assignments_by_id[assignment_id] = assignment

        referenced_definitions: list[dict[str, Any]] = []
        seen_definition_ids: set[str] = set()
        for assignment in assignments_by_id.values():
            definition_id = str(assignment.get("definition_id") or "").strip()
            if not definition_id or definition_id in seen_definition_ids:
                continue
            seen_definition_ids.add(definition_id)
            referenced_definitions.append(self._read_policy_definition(definition_id))

        return {
            "scope_paths": scope_paths,
            "assignment_count": len(assignments_by_id),
            "assignments": list(assignments_by_id.values()),
            "referenced_definition_count": len(referenced_definitions),
            "referenced_definitions": referenced_definitions,
        }

    def _list_policy_assignments(self, scope_path: str) -> list[dict[str, Any]]:
        """Run list policy assignments."""
        next_url = f"{self._management_base_url}{scope_path}/providers/Microsoft.Authorization/policyAssignments"
        assignments: list[dict[str, Any]] = []

        while next_url:
            payload = self._arm_get_json(next_url, api_version="2023-04-01")
            value = payload.get("value")
            if not isinstance(value, list):
                break
            for item in value:
                if not isinstance(item, dict):
                    continue
                raw_props = item.get("properties")
                properties: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
                assignments.append(
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "type": str(item.get("type") or ""),
                        "scope": str(properties.get("scope") or scope_path),
                        "display_name": str(properties.get("displayName") or ""),
                        "description": str(properties.get("description") or ""),
                        "definition_id": str(properties.get("policyDefinitionId") or ""),
                        "enforcement_mode": str(properties.get("enforcementMode") or ""),
                        "not_scopes": list(properties.get("notScopes") or []),
                        "parameters": properties.get("parameters") or {},
                        "metadata": properties.get("metadata") or {},
                        "resource_selectors": properties.get("resourceSelectors") or [],
                        "overrides": properties.get("overrides") or [],
                    }
                )
            next_url = str(payload.get("nextLink") or "").strip()
        return assignments

    def _read_policy_definition(self, definition_id: str) -> dict[str, Any]:
        """Run read policy definition."""
        payload = self._arm_get_json(definition_id, api_version="2023-04-01")
        raw_properties = payload.get("properties")
        properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
        raw_parameters = properties.get("parameters")
        parameters = raw_parameters if isinstance(raw_parameters, dict) else {}
        summary: dict[str, Any] = {
            "id": str(payload.get("id") or definition_id),
            "name": str(payload.get("name") or ""),
            "type": str(payload.get("type") or ""),
            "display_name": str(properties.get("displayName") or ""),
            "description": str(properties.get("description") or ""),
            "policy_type": str(properties.get("policyType") or ""),
            "mode": str(properties.get("mode") or ""),
            "version": str(properties.get("version") or ""),
            "metadata": properties.get("metadata") or {},
            "parameter_names": sorted(parameters.keys()),
        }
        if str(payload.get("type") or "").endswith("/policyDefinitions"):
            raw_policy_rule = properties.get("policyRule")
            policy_rule: dict[str, Any] = (
                raw_policy_rule if isinstance(raw_policy_rule, dict) else {}
            )
            summary["effect"] = self._extract_policy_effect(policy_rule)
        if str(payload.get("type") or "").endswith("/policySetDefinitions"):
            raw_definitions = properties.get("policyDefinitions")
            definitions = raw_definitions if isinstance(raw_definitions, list) else []
            summary["policy_definition_ids"] = [
                str(item.get("policyDefinitionId") or "")
                for item in definitions
                if isinstance(item, dict)
            ]
            summary["policy_definition_count"] = len(summary["policy_definition_ids"])
        return summary

    def _extract_policy_effect(self, policy_rule: dict[str, Any]) -> str:
        """Run extract policy effect."""
        raw_then_clause = policy_rule.get("then")
        then_clause: dict[str, Any] = raw_then_clause if isinstance(raw_then_clause, dict) else {}
        effect = then_clause.get("effect")
        if isinstance(effect, str):
            return effect
        if isinstance(effect, dict):
            return str(effect.get("value") or effect.get("field") or "")
        return ""

    def _arm_get_json(self, path_or_url: str, api_version: str = "2021-04-01") -> dict[str, Any]:
        """Run arm get json."""
        token = self._credential.get_token("https://management.azure.com/.default").token
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            separator = "&" if "?" in path_or_url else "?"
            url = f"{path_or_url}{separator}api-version={api_version}"
        else:
            path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{self._management_base_url}{path}?api-version={api_version}"
        response = self._http_get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Azure ARM response payload must be an object")
        return payload

    def _read_resource_by_id(self, resource_id: str) -> dict[str, Any]:
        """Run read resource by id."""
        payload = self._arm_get_json(resource_id, api_version="2021-04-01")
        return {
            "id": str(payload.get("id") or resource_id),
            "name": str(payload.get("name") or ""),
            "type": str(payload.get("type") or ""),
            "location": str(payload.get("location") or ""),
            "tags": payload.get("tags") or {},
            "properties": payload.get("properties") or {},
            "sku": payload.get("sku") or {},
            "kind": payload.get("kind") or "",
        }

    def _list_resources_in_group(
        self, subscription_id: str, resource_group: str
    ) -> list[dict[str, Any]]:
        """Run list resources in group."""
        next_url = f"{self._management_base_url}/subscriptions/{subscription_id}/resourceGroups/{resource_group}/resources"
        resources: list[dict[str, Any]] = []

        while next_url and len(resources) < self._max_resources:
            payload = self._arm_get_json(next_url, api_version="2021-04-01")
            value = payload.get("value")
            if not isinstance(value, list):
                break
            for item in value:
                if not isinstance(item, dict):
                    continue
                resources.append(
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "type": str(item.get("type") or ""),
                        "location": str(item.get("location") or ""),
                        "tags": item.get("tags") or {},
                        "kind": item.get("kind") or "",
                    }
                )
                if len(resources) >= self._max_resources:
                    break
            next_url = str(payload.get("nextLink") or "").strip()
        return resources
