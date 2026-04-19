"""Unit tests for query_web/auth.py."""
from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace

import pytest
from starlette.requests import Request

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web.auth import (
    _decode_client_principal,
    _group_auth_failure_message,
    _groups_from_client_principal_header,
    _normalise_object_id,
    _principal_has_group_overage,
    _request_groups,
    _split_group_values,
    is_authorised,
    is_authorised_request,
    unauthorised_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_principal(claims: list[dict[str, str]]) -> str:
    payload = json.dumps({"claims": claims}, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii").rstrip("=")


def _make_request(headers: dict[str, str] | None = None) -> Request:
    header_items = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": header_items,
    }
    return Request(scope)


def _config(*, auth_token: str = "", required_group: str = "") -> SimpleNamespace:
    return SimpleNamespace(auth_token=auth_token, required_group_object_id=required_group)


# ---------------------------------------------------------------------------
# _normalise_object_id
# ---------------------------------------------------------------------------


def test_normalise_object_id_lowercases_and_strips() -> None:
    assert _normalise_object_id("  ABC-123  ") == "abc-123"


def test_normalise_object_id_empty_string() -> None:
    assert _normalise_object_id("") == ""


# ---------------------------------------------------------------------------
# _split_group_values
# ---------------------------------------------------------------------------


def test_split_group_values_comma_separated() -> None:
    result = _split_group_values("AAA,BBB,CCC")
    assert result == {"aaa", "bbb", "ccc"}


def test_split_group_values_semicolon_and_spaces() -> None:
    result = _split_group_values("AAA; BBB  CCC")
    assert result == {"aaa", "bbb", "ccc"}


def test_split_group_values_empty_string() -> None:
    assert _split_group_values("") == set()


def test_split_group_values_whitespace_only() -> None:
    assert _split_group_values("   ") == set()


# ---------------------------------------------------------------------------
# _decode_client_principal
# ---------------------------------------------------------------------------


def test_decode_client_principal_returns_dict() -> None:
    data = {"claims": [{"typ": "groups", "val": "abc"}]}
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    result = _decode_client_principal(encoded)
    assert result == data


def test_decode_client_principal_handles_missing_padding() -> None:
    data = {"claims": []}
    raw = base64.b64encode(json.dumps(data).encode()).decode().rstrip("=")
    result = _decode_client_principal(raw)
    assert isinstance(result, dict)


def test_decode_client_principal_empty_string_returns_none() -> None:
    assert _decode_client_principal("") is None


def test_decode_client_principal_invalid_base64_returns_none() -> None:
    assert _decode_client_principal("not-valid!!!") is None


def test_decode_client_principal_non_dict_json_returns_none() -> None:
    encoded = base64.b64encode(b"[1,2,3]").decode()
    assert _decode_client_principal(encoded) is None


# ---------------------------------------------------------------------------
# _groups_from_client_principal_header
# ---------------------------------------------------------------------------


def test_groups_from_header_extracts_groups_claim() -> None:
    encoded = _encode_principal([{"typ": "groups", "val": "AAA-111"}])
    groups = _groups_from_client_principal_header(encoded)
    assert "aaa-111" in groups


def test_groups_from_header_handles_long_claim_type() -> None:
    encoded = _encode_principal([
        {
            "typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
            "val": "GRP-001;GRP-002",
        }
    ])
    groups = _groups_from_client_principal_header(encoded)
    assert "grp-001" in groups
    assert "grp-002" in groups


def test_groups_from_header_ignores_non_group_claims() -> None:
    encoded = _encode_principal([{"typ": "name", "val": "alice"}])
    groups = _groups_from_client_principal_header(encoded)
    assert groups == set()


def test_groups_from_header_empty_val_skipped() -> None:
    encoded = _encode_principal([{"typ": "groups", "val": ""}])
    assert _groups_from_client_principal_header(encoded) == set()


def test_groups_from_header_empty_string_returns_empty() -> None:
    assert _groups_from_client_principal_header("") == set()


def test_groups_from_header_non_list_claims_returns_empty() -> None:
    data = {"claims": "not-a-list"}
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    assert _groups_from_client_principal_header(encoded) == set()


# ---------------------------------------------------------------------------
# _principal_has_group_overage
# ---------------------------------------------------------------------------


def test_principal_has_group_overage_detects_hasgroups_claim() -> None:
    encoded = _encode_principal([
        {"typ": "hasgroups", "val": "true"},
        {"typ": "name", "val": "alice"},
    ])
    assert _principal_has_group_overage(encoded) is True


def test_principal_has_group_overage_detects_claim_names() -> None:
    encoded = _encode_principal([{"typ": "_claim_names", "val": "groups"}])
    assert _principal_has_group_overage(encoded) is True


def test_principal_has_group_overage_false_for_normal_principal() -> None:
    encoded = _encode_principal([{"typ": "groups", "val": "grp-123"}])
    assert _principal_has_group_overage(encoded) is False


def test_principal_has_group_overage_false_for_empty_string() -> None:
    assert _principal_has_group_overage("") is False


# ---------------------------------------------------------------------------
# _request_groups
# ---------------------------------------------------------------------------


def test_request_groups_none_returns_empty() -> None:
    assert _request_groups(None) == set()


def test_request_groups_reads_client_principal_header() -> None:
    encoded = _encode_principal([{"typ": "groups", "val": "GRP-AAA"}])
    request = _make_request({"x-ms-client-principal": encoded})
    groups = _request_groups(request)
    assert "grp-aaa" in groups


def test_request_groups_falls_back_to_flat_groups_header() -> None:
    request = _make_request({"x-ms-client-principal-groups": "GRP-AAA,GRP-BBB"})
    groups = _request_groups(request)
    assert "grp-aaa" in groups
    assert "grp-bbb" in groups


def test_request_groups_prefers_principal_header_over_flat() -> None:
    encoded = _encode_principal([{"typ": "groups", "val": "FROM-PRINCIPAL"}])
    request = _make_request({
        "x-ms-client-principal": encoded,
        "x-ms-client-principal-groups": "FROM-FLAT",
    })
    groups = _request_groups(request)
    assert "from-principal" in groups


# ---------------------------------------------------------------------------
# _group_auth_failure_message
# ---------------------------------------------------------------------------


def test_group_auth_failure_message_no_principal_headers() -> None:
    message = _group_auth_failure_message(_make_request())
    assert "No Entra ID principal headers" in message


def test_group_auth_failure_message_overage() -> None:
    encoded = _encode_principal([
        {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": "x"},
        {"typ": "hasgroups", "val": "true"},
    ])
    request = _make_request({"x-ms-client-principal": encoded})
    message = _group_auth_failure_message(request)
    assert "overage" in message.lower() or "group overage" in message.lower()


def test_group_auth_failure_message_authenticated_no_groups() -> None:
    # Principal present but has no group claims
    encoded = _encode_principal([{"typ": "name", "val": "alice"}])
    request = _make_request({
        "x-ms-client-principal": encoded,
        "x-ms-client-principal-id": "some-id",
    })
    message = _group_auth_failure_message(request)
    assert "group claims" in message.lower() or "no group" in message.lower()


def test_group_auth_failure_message_group_present_wrong_group() -> None:
    encoded = _encode_principal([{"typ": "groups", "val": "wrong-group"}])
    request = _make_request({"x-ms-client-principal": encoded})
    message = _group_auth_failure_message(request)
    assert "not in the required" in message.lower() or "security group" in message.lower()


def test_group_auth_failure_message_none_request() -> None:
    message = _group_auth_failure_message(None)
    assert "unavailable" in message.lower() or "context" in message.lower()


# ---------------------------------------------------------------------------
# is_authorised (no request context)
# ---------------------------------------------------------------------------


def test_is_authorised_passes_when_no_token_and_no_group_configured() -> None:
    cfg = _config()
    assert is_authorised("", cfg) is True


def test_is_authorised_passes_with_correct_token_no_group() -> None:
    cfg = _config(auth_token="secret")
    assert is_authorised("secret", cfg) is True


def test_is_authorised_fails_with_wrong_token() -> None:
    cfg = _config(auth_token="secret")
    assert is_authorised("wrong", cfg) is False


def test_is_authorised_returns_false_when_group_required_and_no_request() -> None:
    # Without request context, group auth cannot be satisfied
    cfg = _config(required_group="some-group-id")
    assert is_authorised("", cfg) is False


# ---------------------------------------------------------------------------
# is_authorised_request (full check)
# ---------------------------------------------------------------------------


def test_is_authorised_request_no_auth_configured() -> None:
    cfg = _config()
    assert is_authorised_request("", _make_request(), cfg) is True


def test_is_authorised_request_token_mismatch_denied() -> None:
    cfg = _config(auth_token="secret")
    assert is_authorised_request("wrong", _make_request(), cfg) is False


def test_is_authorised_request_correct_token_no_group() -> None:
    cfg = _config(auth_token="secret")
    assert is_authorised_request("secret", _make_request(), cfg) is True


def test_is_authorised_request_group_present_and_matches() -> None:
    gid = "7c110a48-68ac-4514-ae8f-1f674091b559"
    encoded = _encode_principal([{"typ": "groups", "val": gid.upper()}])
    request = _make_request({"x-ms-client-principal": encoded})
    cfg = _config(required_group=gid)
    assert is_authorised_request("", request, cfg) is True


def test_is_authorised_request_group_required_but_missing() -> None:
    cfg = _config(required_group="7c110a48-68ac-4514-ae8f-1f674091b559")
    assert is_authorised_request("", _make_request(), cfg) is False


def test_is_authorised_request_none_request_with_group_required() -> None:
    cfg = _config(required_group="some-group-id")
    assert is_authorised_request("", None, cfg) is False


# ---------------------------------------------------------------------------
# unauthorised_message
# ---------------------------------------------------------------------------


def test_unauthorised_message_with_group_configured_returns_entra_reason() -> None:
    cfg = _config(required_group="some-group-id")
    msg = unauthorised_message(_make_request(), cfg)
    assert "Unauthorised" in msg


def test_unauthorised_message_without_group_configured_returns_token_message() -> None:
    cfg = _config()
    msg = unauthorised_message(_make_request(), cfg)
    assert "token" in msg.lower() or "Unauthorised" in msg
