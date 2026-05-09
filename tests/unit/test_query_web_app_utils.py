"""Unit tests for pure utility helpers in query_web/app.py.

Covers: _is_allowed_filetype, _extension_matches_mime, _risk_label,
_normalise_object_id, _split_group_values, _decode_client_principal,
_groups_from_client_principal_header, _principal_has_group_overage,
_request_groups, _group_auth_failure_message, _is_authorised_request,
_unauthorised_message, _target_env_name, _diagnostics_enabled.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_COSMOS_ENDPOINT", "https://test.documents.azure.com")
os.environ.setdefault("AZURE_COSMOS_DATABASE_NAME", "rag-conversations")
os.environ.setdefault("AZURE_COSMOS_CONTAINER_NAME", "conversations")

from query_web import app as app_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(headers: dict[str, str] | None = None) -> Mock:
    """Create a minimal mock request whose .headers.get() respects the dict."""
    h = headers or {}
    req = Mock()
    req.headers.get = lambda key, default="": h.get(key, default)
    return req


def _encode_principal(claims: list[dict[str, str]]) -> str:
    """Base64-encode an Azure Static Web Apps principal payload."""
    payload = {"claims": claims}
    return base64.b64encode(json.dumps(payload).encode()).decode()


# ---------------------------------------------------------------------------
# _is_allowed_filetype
# ---------------------------------------------------------------------------


def test_is_allowed_filetype_pdf_accepted() -> None:
    assert app_module._is_allowed_filetype("report.pdf") is True


def test_is_allowed_filetype_exe_rejected() -> None:
    assert app_module._is_allowed_filetype("malware.exe") is False


def test_is_allowed_filetype_no_extension_rejected() -> None:
    assert app_module._is_allowed_filetype("Makefile") is False


def test_is_allowed_filetype_case_insensitive() -> None:
    assert app_module._is_allowed_filetype("report.PDF") is True


# ---------------------------------------------------------------------------
# _extension_matches_mime
# ---------------------------------------------------------------------------


def test_extension_matches_mime_pdf_match() -> None:
    assert app_module._extension_matches_mime("file.pdf", "application/pdf") is True


def test_extension_matches_mime_pdf_mismatch() -> None:
    assert app_module._extension_matches_mime("file.pdf", "text/plain") is False


def test_extension_matches_mime_unknown_extension_false() -> None:
    assert app_module._extension_matches_mime("file.xyz", "application/octet-stream") is False


def test_extension_matches_mime_strips_charset_parameter() -> None:
    # HTML includes a charset parameter in some browsers
    assert app_module._extension_matches_mime("page.html", "text/html; charset=utf-8") is True


# ---------------------------------------------------------------------------
# _risk_label
# ---------------------------------------------------------------------------


def test_risk_label_low() -> None:
    assert app_module._risk_label("low") == "Low"


def test_risk_label_medium() -> None:
    assert app_module._risk_label("medium") == "Medium"


def test_risk_label_high() -> None:
    assert app_module._risk_label("high") == "High"


def test_risk_label_critical() -> None:
    assert app_module._risk_label("critical") == "Critical"


def test_risk_label_unknown_value() -> None:
    assert app_module._risk_label("extreme") == "Unknown"


def test_risk_label_underscored_value() -> None:
    assert app_module._risk_label("low_risk") == "Unknown"


def test_risk_label_empty_string_defaults_unknown() -> None:
    assert app_module._risk_label("") == "Unknown"


def test_risk_label_case_insensitive() -> None:
    assert app_module._risk_label("HIGH") == "High"


# ---------------------------------------------------------------------------
# _normalise_object_id
# ---------------------------------------------------------------------------


def test_normalise_object_id_strips_and_lowercases() -> None:
    assert app_module._normalise_object_id("  ABC-123  ") == "abc-123"


def test_normalise_object_id_already_clean() -> None:
    assert app_module._normalise_object_id("abc") == "abc"


# ---------------------------------------------------------------------------
# _split_group_values
# ---------------------------------------------------------------------------


def test_split_group_values_comma_separated() -> None:
    result = app_module._split_group_values("a,b,c")
    assert result == {"a", "b", "c"}


def test_split_group_values_space_separated() -> None:
    result = app_module._split_group_values("a b c")
    assert result == {"a", "b", "c"}


def test_split_group_values_mixed_separators() -> None:
    result = app_module._split_group_values("A; B,C")
    assert result == {"a", "b", "c"}


def test_split_group_values_empty_string() -> None:
    assert app_module._split_group_values("") == set()


# ---------------------------------------------------------------------------
# _decode_client_principal
# ---------------------------------------------------------------------------


def test_decode_client_principal_valid_payload() -> None:
    claims = [{"typ": "groups", "val": "group-id-1"}]
    encoded = _encode_principal(claims)
    result = app_module._decode_client_principal(encoded)
    assert isinstance(result, dict)
    assert result["claims"] == claims


def test_decode_client_principal_empty_string_returns_none() -> None:
    assert app_module._decode_client_principal("") is None


def test_decode_client_principal_malformed_base64_returns_none() -> None:
    assert app_module._decode_client_principal("!!!not-base64!!!") is None


def test_decode_client_principal_non_dict_json_returns_none() -> None:
    encoded = base64.b64encode(b"[1,2,3]").decode()
    assert app_module._decode_client_principal(encoded) is None


# ---------------------------------------------------------------------------
# _resolve_writable_sqlite_path
# ---------------------------------------------------------------------------


def test_resolve_writable_sqlite_path_prefers_first_writable_candidate(tmp_path) -> None:
    preferred = str(tmp_path / "a" / "local_state.db")
    secondary = str(tmp_path / "b" / "local_state.db")

    result = app_module._resolve_writable_sqlite_path(preferred, secondary)

    assert result == preferred


def test_resolve_writable_sqlite_path_skips_unwritable_candidate(tmp_path) -> None:
    unwritable = "/proc/1/query-web-local-state.db"
    writable = str(tmp_path / "fallback" / "local_state.db")

    result = app_module._resolve_writable_sqlite_path(unwritable, writable)

    assert result == writable


def test_resolve_writable_sqlite_path_returns_none_when_no_candidate_writable() -> None:
    result = app_module._resolve_writable_sqlite_path(
        "/proc/1/query-web-local-state.db",
        "/sys/kernel/query-web-local-state.db",
    )

    assert result is None


def test_resolve_writable_sqlite_path_accepts_memory() -> None:
    assert app_module._resolve_writable_sqlite_path(":memory:") == ":memory:"


# ---------------------------------------------------------------------------
# _groups_from_client_principal_header
# ---------------------------------------------------------------------------


def test_groups_from_header_returns_group_ids() -> None:
    claims = [{"typ": "groups", "val": "group-a"}, {"typ": "groups", "val": "group-b"}]
    encoded = _encode_principal(claims)
    result = app_module._groups_from_client_principal_header(encoded)
    assert "group-a" in result
    assert "group-b" in result


def test_groups_from_header_empty_string_returns_empty_set() -> None:
    assert app_module._groups_from_client_principal_header("") == set()


def test_groups_from_header_malformed_returns_empty_set() -> None:
    assert app_module._groups_from_client_principal_header("!!bad!!") == set()


def test_groups_from_header_non_group_claims_ignored() -> None:
    claims = [{"typ": "email", "val": "user@example.com"}]
    encoded = _encode_principal(claims)
    assert app_module._groups_from_client_principal_header(encoded) == set()


def test_groups_from_header_full_urn_typ_accepted() -> None:
    claims = [
        {
            "typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
            "val": "grp-xyz",
        }
    ]
    encoded = _encode_principal(claims)
    result = app_module._groups_from_client_principal_header(encoded)
    assert "grp-xyz" in result


def test_groups_from_header_skips_claim_with_empty_val() -> None:
    claims = [{"typ": "groups", "val": ""}]
    encoded = _encode_principal(claims)
    assert app_module._groups_from_client_principal_header(encoded) == set()


def test_groups_from_header_non_list_claims_returns_empty() -> None:
    payload = {"claims": "not-a-list"}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    assert app_module._groups_from_client_principal_header(encoded) == set()


# ---------------------------------------------------------------------------
# _principal_has_group_overage
# ---------------------------------------------------------------------------


def test_principal_has_group_overage_false_when_no_overage_claims() -> None:
    claims = [{"typ": "groups", "val": "grp-a"}]
    encoded = _encode_principal(claims)
    assert app_module._principal_has_group_overage(encoded) is False


def test_principal_has_group_overage_true_on_hasgroups_claim() -> None:
    claims = [{"typ": "hasgroups", "val": "true"}]
    encoded = _encode_principal(claims)
    assert app_module._principal_has_group_overage(encoded) is True


def test_principal_has_group_overage_true_on_groups_link() -> None:
    claims = [{"typ": "http://schemas.microsoft.com/claims/groups.link", "val": "https://graph..."}]
    encoded = _encode_principal(claims)
    assert app_module._principal_has_group_overage(encoded) is True


def test_principal_has_group_overage_empty_principal_false() -> None:
    assert app_module._principal_has_group_overage("") is False


def test_principal_has_group_overage_non_list_claims_false() -> None:
    payload = {"claims": "bad"}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    assert app_module._principal_has_group_overage(encoded) is False


# ---------------------------------------------------------------------------
# _request_groups
# ---------------------------------------------------------------------------


def test_request_groups_none_request_returns_empty() -> None:
    assert app_module._request_groups(None) == set()


def test_request_groups_from_principal_header() -> None:
    claims = [{"typ": "groups", "val": "grp-123"}]
    encoded = _encode_principal(claims)
    req = _make_request({"x-ms-client-principal": encoded})
    result = app_module._request_groups(req)
    assert "grp-123" in result


def test_request_groups_from_flat_groups_header() -> None:
    req = _make_request(
        {"x-ms-client-principal": "", "x-ms-client-principal-groups": "grp-a,grp-b"}
    )
    result = app_module._request_groups(req)
    assert "grp-a" in result
    assert "grp-b" in result


def test_request_groups_no_headers_returns_empty() -> None:
    req = _make_request({})
    assert app_module._request_groups(req) == set()


# ---------------------------------------------------------------------------
# _group_auth_failure_message
# ---------------------------------------------------------------------------


def test_group_auth_failure_message_none_request() -> None:
    msg = app_module._group_auth_failure_message(None)
    assert "unavailable" in msg.lower()


def test_group_auth_failure_message_no_principal_context() -> None:
    req = _make_request({})
    msg = app_module._group_auth_failure_message(req)
    assert "platform sign-in" in msg.lower() or "no entra" in msg.lower()


def test_group_auth_failure_message_overage() -> None:
    claims = [{"typ": "hasgroups", "val": "true"}]
    encoded = _encode_principal(claims)
    req = _make_request({"x-ms-client-principal": encoded})
    msg = app_module._group_auth_failure_message(req)
    assert "overage" in msg.lower()


def test_group_auth_failure_message_no_group_claims() -> None:
    claims = [{"typ": "email", "val": "user@example.com"}]
    encoded = _encode_principal(claims)
    req = _make_request({"x-ms-client-principal": encoded, "x-ms-client-principal-id": "user-id"})
    msg = app_module._group_auth_failure_message(req)
    assert "group" in msg.lower()


def test_group_auth_failure_message_has_groups_not_required() -> None:
    claims = [{"typ": "groups", "val": "other-group"}]
    encoded = _encode_principal(claims)
    req = _make_request({"x-ms-client-principal": encoded})
    msg = app_module._group_auth_failure_message(req)
    assert "unauthorised" in msg.lower() or "required" in msg.lower()


# ---------------------------------------------------------------------------
# _is_authorised_request  (no required group configured)
# ---------------------------------------------------------------------------


def _cfg(**overrides):  # type: ignore[no-untyped-def]
    """Return a copy of app_module.config with selected fields replaced."""
    import dataclasses

    return dataclasses.replace(app_module.config, **overrides)


def test_is_authorised_request_no_group_required_returns_true() -> None:
    cfg = _cfg(required_group_object_id="", auth_token="")
    with patch.object(app_module, "config", cfg):
        assert app_module._is_authorised_request("any-token", None) is True


def test_is_authorised_request_wrong_shared_token_returns_false() -> None:
    cfg = _cfg(auth_token="secret", required_group_object_id="")
    with patch.object(app_module, "config", cfg):
        assert app_module._is_authorised_request("wrong", None) is False


def test_is_authorised_request_correct_group_returns_true() -> None:
    group_id = "required-group-abc"
    cfg = _cfg(required_group_object_id=group_id, auth_token="")
    with patch.object(app_module, "config", cfg):
        claims = [{"typ": "groups", "val": group_id}]
        encoded = _encode_principal(claims)
        req = _make_request({"x-ms-client-principal": encoded})
        assert app_module._is_authorised_request("", req) is True


def test_is_authorised_request_group_required_no_request_false() -> None:
    cfg = _cfg(required_group_object_id="grp-x", auth_token="")
    with patch.object(app_module, "config", cfg):
        assert app_module._is_authorised_request("", None) is False


# ---------------------------------------------------------------------------
# _unauthorised_message
# ---------------------------------------------------------------------------


def test_unauthorised_message_no_group_required() -> None:
    cfg = _cfg(required_group_object_id="")
    with patch.object(app_module, "config", cfg):
        msg = app_module._unauthorised_message()
        assert "token" in msg.lower()


def test_unauthorised_message_group_required_delegates() -> None:
    cfg = _cfg(required_group_object_id="grp")
    with patch.object(app_module, "config", cfg):
        msg = app_module._unauthorised_message(None)
        assert "unauthorised" in msg.lower()


# ---------------------------------------------------------------------------
# _target_env_name
# ---------------------------------------------------------------------------


def test_target_env_name_default_is_dev() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TARGET_ENV", None)
        os.environ.pop("ENV", None)
        assert app_module._target_env_name() == "dev"


def test_target_env_name_uses_target_env() -> None:
    with patch.dict(os.environ, {"TARGET_ENV": "prod"}):
        assert app_module._target_env_name() == "prod"


def test_target_env_name_falls_back_to_env() -> None:
    env = {k: v for k, v in os.environ.items() if k != "TARGET_ENV"}
    env["ENV"] = "staging"
    with patch.dict(os.environ, env, clear=True):
        assert app_module._target_env_name() == "staging"


# ---------------------------------------------------------------------------
# _diagnostics_enabled
# ---------------------------------------------------------------------------


def test_diagnostics_enabled_in_dev() -> None:
    with patch.dict(os.environ, {"TARGET_ENV": "dev"}):
        assert app_module._diagnostics_enabled() is True


def test_diagnostics_disabled_in_prod() -> None:
    with patch.dict(os.environ, {"TARGET_ENV": "prod"}):
        assert app_module._diagnostics_enabled() is False


# ---------------------------------------------------------------------------
# _branding_ctx
# ---------------------------------------------------------------------------


def test_branding_ctx_contains_app_title_and_static_version() -> None:
    ctx = app_module._branding_ctx()
    assert "app_title" in ctx
    assert "static_version" in ctx


# ---------------------------------------------------------------------------
# _is_authorised  (legacy shared-token helper)
# ---------------------------------------------------------------------------


def test_is_authorised_wrong_token_returns_false() -> None:
    cfg = _cfg(auth_token="secret", required_group_object_id="")
    with patch.object(app_module, "config", cfg):
        assert app_module._is_authorised("wrong") is False


def test_is_authorised_no_auth_token_no_group_returns_true() -> None:
    cfg = _cfg(auth_token="", required_group_object_id="")
    with patch.object(app_module, "config", cfg):
        assert app_module._is_authorised("anything") is True


def test_is_authorised_group_required_returns_false() -> None:
    cfg = _cfg(auth_token="", required_group_object_id="some-group")
    with patch.object(app_module, "config", cfg):
        assert app_module._is_authorised("anything") is False


# ---------------------------------------------------------------------------
# _groups_from_client_principal_header — non-dict claim branch
# ---------------------------------------------------------------------------


def test_groups_from_header_skips_non_dict_claims() -> None:
    # Mixed list: non-dict items should be skipped via `continue`
    claims = ["not-a-dict", 42, {"typ": "groups", "val": "grp-abc"}]
    encoded = _encode_principal(claims)
    result = app_module._groups_from_client_principal_header(encoded)
    assert "grp-abc" in result


# ---------------------------------------------------------------------------
# _principal_has_group_overage — non-dict claim branch
# ---------------------------------------------------------------------------


def test_principal_has_group_overage_skips_non_dict_then_finds_overage() -> None:
    # Non-dict items skipped; overage claim detected afterwards
    claims = ["not-a-dict", {"typ": "hasgroups", "val": "true"}]
    encoded = _encode_principal(claims)
    assert app_module._principal_has_group_overage(encoded) is True


# ---------------------------------------------------------------------------
# _check_diagnostics_access
# ---------------------------------------------------------------------------


def test_check_diagnostics_access_authorised_dev_returns_none() -> None:
    cfg = _cfg(required_group_object_id="", auth_token="")
    with patch.object(app_module, "config", cfg):
        with patch.dict(os.environ, {"TARGET_ENV": "dev"}):
            req = _make_request({})
            result = app_module._check_diagnostics_access(req, "")
            assert result is None


def test_check_diagnostics_access_unauthorised_returns_401() -> None:
    cfg = _cfg(required_group_object_id="", auth_token="secret")
    with patch.object(app_module, "config", cfg):
        req = _make_request({})
        result = app_module._check_diagnostics_access(req, "wrong-token")
        assert result is not None
        assert result.status_code == 401


def test_check_diagnostics_access_authorised_prod_returns_403() -> None:
    cfg = _cfg(required_group_object_id="", auth_token="")
    with patch.object(app_module, "config", cfg):
        with patch.dict(os.environ, {"TARGET_ENV": "prod"}):
            req = _make_request({})
            result = app_module._check_diagnostics_access(req, "")
            assert result is not None
            assert result.status_code == 403


# ---------------------------------------------------------------------------
# _resolve_acr_registry_name
# ---------------------------------------------------------------------------

_ACR_ENV_KEYS = [
    "ACR_NAME",
    "AZURE_CONTAINER_REGISTRY_NAME",
    "CONTAINER_REGISTRY_NAME",
    "ACR_LOGIN_SERVER",
    "AZURE_CONTAINER_REGISTRY_LOGIN_SERVER",
    "CONTAINER_REGISTRY_LOGIN_SERVER",
]


def _clear_acr_env() -> dict[str, str]:
    return {k: "" for k in _ACR_ENV_KEYS}


def test_resolve_acr_registry_name_explicit_takes_priority() -> None:
    with patch.dict(os.environ, _clear_acr_env()):
        assert app_module._resolve_acr_registry_name("myregistry") == "myregistry"


def test_resolve_acr_registry_name_from_acr_name_env() -> None:
    env = {**_clear_acr_env(), "ACR_NAME": "fromenv"}
    with patch.dict(os.environ, env):
        assert app_module._resolve_acr_registry_name("") == "fromenv"


def test_resolve_acr_registry_name_from_login_server() -> None:
    env = {**_clear_acr_env(), "ACR_LOGIN_SERVER": "myregistry.azurecr.io"}
    with patch.dict(os.environ, env):
        assert app_module._resolve_acr_registry_name("") == "myregistry"


def test_resolve_acr_registry_name_ignores_non_azurecr_login_server() -> None:
    env = {**_clear_acr_env(), "ACR_LOGIN_SERVER": "myregistry.example.com"}
    with patch.dict(os.environ, env):
        assert app_module._resolve_acr_registry_name("") == ""


def test_resolve_acr_registry_name_empty_when_nothing_set() -> None:
    with patch.dict(os.environ, _clear_acr_env()):
        assert app_module._resolve_acr_registry_name("") == ""


def test_startup_disables_conversation_persistence_when_local_state_unwritable() -> None:
    tracked_keys = ["AZURE_COSMOS_ENDPOINT", "CLOUD_PROVIDER", "LOCAL_STATE_DB_PATH"]
    previous = {key: os.environ.get(key) for key in tracked_keys}

    try:
        os.environ["AZURE_COSMOS_ENDPOINT"] = ""
        os.environ["CLOUD_PROVIDER"] = "local"
        os.environ["LOCAL_STATE_DB_PATH"] = "/app/runtime/out/local_state.db"

        with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
                reloaded = importlib.reload(app_module)

        assert reloaded.conversations_container is None
        assert reloaded._local_state_db_path == ""
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(app_module)
