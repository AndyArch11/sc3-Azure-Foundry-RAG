"""Unit tests for query_web/endpoints/home.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from query_web.endpoints.home import register_home_endpoints

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    defaults = dict(
        search_top_k=5,
        default_temperature=0.2,
        max_completion_tokens=1400,
        evaluator_max_completion_tokens=800,
        controls_semantic_default=True,
        search_index_name="grounding-index",
        embedding_deployment="text-embedding-ada-002",
        query_deployment="gpt-4",
        evaluation_threshold=0.72,
        auth_token="",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_templates(rendered: dict | None = None):
    tpl = Mock()
    tpl.TemplateResponse.return_value = Mock(status_code=200)
    return tpl


def _make_request(headers: dict | None = None):
    req = Mock()
    req.headers.get = lambda k, d="": (headers or {}).get(k, d)
    return req


def _app_with_home(
    *,
    is_authorised=True,
    config=None,
    templates=None,
    unauthorised_message=None,
    branding_ctx=None,
):
    """Register home endpoint on a minimal stub app and return (app, route_fn)."""
    app = MagicMock()
    registered: list = []

    def _get(path, **kwargs):
        def decorator(fn):
            registered.append(fn)
            return fn

        return decorator

    app.get = _get

    cfg = config or _make_config()
    tpl = templates or _make_templates()
    unauth_msg = unauthorised_message or (lambda req: "Unauthorised.")
    brand_ctx = branding_ctx or (lambda: {"app_title": "Test", "static_version": "1"})

    register_home_endpoints(
        app,
        is_authorised_request=lambda token, req: is_authorised,
        unauthorised_message=unauth_msg,
        config=cfg,
        templates=tpl,
        branding_ctx=brand_ctx,
    )

    assert registered, "home() was not registered"
    return app, registered[0], tpl


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def test_home_returns_401_when_not_authorised():
    _, home, _ = _app_with_home(is_authorised=False)
    response = home(_make_request())
    assert response.status_code == 401


def test_home_returns_401_with_string_unauthorised_message():
    app = MagicMock()
    registered: list = []

    def _get(path, **kwargs):
        def decorator(fn):
            registered.append(fn)
            return fn

        return decorator

    app.get = _get
    register_home_endpoints(
        app,
        is_authorised_request=lambda token, req: False,
        unauthorised_message="Access denied.",
        config=_make_config(),
        templates=_make_templates(),
        branding_ctx=lambda: {},
    )
    response = registered[0](_make_request())
    assert response.status_code == 401


def test_home_renders_template_when_authorised():
    _, home, tpl = _app_with_home(is_authorised=True)
    home(_make_request())
    tpl.TemplateResponse.assert_called_once()
    _, kwargs = tpl.TemplateResponse.call_args
    ctx = kwargs if kwargs else tpl.TemplateResponse.call_args[0][2]
    # positional call: (request, template_name, context)
    call_args = tpl.TemplateResponse.call_args
    context = call_args[0][2] if len(call_args[0]) >= 3 else call_args[1].get("context", {})


def test_home_template_context_has_expected_keys():
    _, home, tpl = _app_with_home(is_authorised=True)
    home(_make_request())
    call_args = tpl.TemplateResponse.call_args[0]
    # call signature: TemplateResponse(request, "index.html", context_dict)
    context = call_args[2]
    for key in (
        "question",
        "answer",
        "results",
        "controls_results",
        "retrieve_k",
        "temperature",
        "max_completion_tokens",
        "index_name",
        "query_deployment",
        "query_model_display",
        "auth_enabled",
    ):
        assert key in context, f"Missing context key: {key}"


def test_home_query_model_display_uses_ollama_model_in_local_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma3:27b")

    _, home, tpl = _app_with_home(is_authorised=True, config=_make_config(query_deployment="gpt-4"))
    home(_make_request())
    context = tpl.TemplateResponse.call_args[0][2]
    assert context["query_model_display"] == "gemma3:27b"


def test_home_context_retrieve_k_matches_config():
    _, home, tpl = _app_with_home(is_authorised=True, config=_make_config(search_top_k=10))
    home(_make_request())
    context = tpl.TemplateResponse.call_args[0][2]
    assert context["retrieve_k"] == 10


def test_home_context_auth_enabled_false_when_no_token():
    _, home, tpl = _app_with_home(is_authorised=True, config=_make_config(auth_token=""))
    home(_make_request())
    context = tpl.TemplateResponse.call_args[0][2]
    assert context["auth_enabled"] is False


def test_home_context_auth_enabled_true_when_token_set():
    _, home, tpl = _app_with_home(is_authorised=True, config=_make_config(auth_token="secret"))
    home(_make_request())
    context = tpl.TemplateResponse.call_args[0][2]
    assert context["auth_enabled"] is True


def test_home_returns_500_when_templates_none():
    app = MagicMock()
    registered: list = []

    def _get(path, **kwargs):
        def decorator(fn):
            registered.append(fn)
            return fn

        return decorator

    app.get = _get
    register_home_endpoints(
        app,
        is_authorised_request=lambda token, req: True,
        templates=None,
        config=_make_config(),
        branding_ctx=lambda: {},
    )
    response = registered[0](_make_request())
    assert response.status_code == 500


def test_home_returns_500_when_config_none():
    app = MagicMock()
    registered: list = []

    def _get(path, **kwargs):
        def decorator(fn):
            registered.append(fn)
            return fn

        return decorator

    app.get = _get
    register_home_endpoints(
        app,
        is_authorised_request=lambda token, req: True,
        templates=_make_templates(),
        config=None,
        branding_ctx=lambda: {},
    )
    response = registered[0](_make_request())
    assert response.status_code == 500


def test_home_svc_resolution_fallback():
    """When svc provides dependencies, they are resolved from svc attributes."""
    app = MagicMock()
    registered: list = []

    def _get(path, **kwargs):
        def decorator(fn):
            registered.append(fn)
            return fn

        return decorator

    app.get = _get

    tpl = _make_templates()
    cfg = _make_config()

    svc = SimpleNamespace(
        templates=tpl,
        config=cfg,
        _is_authorised_request=lambda token, req: True,
        _unauthorised_message=lambda req: "no",
        _branding_ctx=lambda: {"app_title": "T", "static_version": "1"},
    )

    register_home_endpoints(app, svc=svc)
    registered[0](_make_request())
    tpl.TemplateResponse.assert_called_once()
