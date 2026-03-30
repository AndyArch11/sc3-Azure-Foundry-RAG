from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from starlette.requests import Request

from query_web import app as app_module
from query_web.app import (
    _group_auth_failure_message,
    _groups_from_client_principal_header,
    _is_authorized_request,
)


def _encode_principal(claims: list[dict[str, str]]) -> str:
    payload = json.dumps({"claims": claims}, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii").rstrip("=")


def _make_request(headers: dict[str, str] | None = None) -> Request:
    header_items = []
    for name, value in (headers or {}).items():
        header_items.append((name.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": header_items,
    }
    return Request(scope)


def test_groups_from_client_principal_header_normalizes_claim_values() -> None:
    encoded = _encode_principal(
        [
            {"typ": "groups", "val": "7C110A48-68AC-4514-AE8F-1F674091B559"},
            {
                "typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
                "val": "11111111-1111-1111-1111-111111111111;22222222-2222-2222-2222-222222222222",
            },
        ]
    )

    groups = _groups_from_client_principal_header(encoded)

    assert "7c110a48-68ac-4514-ae8f-1f674091b559" in groups
    assert "11111111-1111-1111-1111-111111111111" in groups
    assert "22222222-2222-2222-2222-222222222222" in groups


def test_is_authorized_request_accepts_case_mismatched_group_ids() -> None:
    request = _make_request(
        {
            "x-ms-client-principal": _encode_principal(
                [{"typ": "groups", "val": "7C110A48-68AC-4514-AE8F-1F674091B559"}]
            )
        }
    )

    patched_config = replace(
        app_module.config,
        required_group_object_id="7c110a48-68ac-4514-ae8f-1f674091b559",
        auth_token="",
    )
    with patch.object(app_module, "config", patched_config):
        assert _is_authorized_request("", request) is True


def test_group_auth_failure_message_reports_missing_principal_headers() -> None:
    request = _make_request()

    message = _group_auth_failure_message(request)

    assert "No Entra ID principal headers were forwarded" in message


def test_group_auth_failure_message_reports_group_overage() -> None:
    request = _make_request(
        {
            "x-ms-client-principal": _encode_principal(
                [
                    {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": "abc"},
                    {"typ": "hasgroups", "val": "true"},
                ]
            )
        }
    )

    message = _group_auth_failure_message(request)

    assert "group overage" in message.lower()
