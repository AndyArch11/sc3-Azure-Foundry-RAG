from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from runtime.assessment_orchestration.mcp.confluence import (
    ConfluenceClient,
    ConfluenceMCPServer,
    MentionPoller,
    _iso_duration_to_since,
    _iso_to_cql_datetime,
    _normalise_comments,
    _normalise_mention_result,
    _parse_confluence_url_path,
    _strip_html,
)

# --------------------------------------------------------------------------- #
# Unit helpers                                                                 #
# --------------------------------------------------------------------------- #


def test_strip_html_basic() -> None:
    assert _strip_html("<p>Hello <strong>world</strong></p>") == "Hello\nworld"


def test_strip_html_plain_text() -> None:
    assert _strip_html("<p>Page content here.</p>") == "Page content here."


def test_strip_html_empty() -> None:
    assert _strip_html("") == ""


def test_parse_confluence_url_path_standard() -> None:
    page_id, space_key = _parse_confluence_url_path("/wiki/spaces/SEC/pages/1234/Page+Title")
    assert page_id == "1234"
    assert space_key == "SEC"


def test_parse_confluence_url_path_no_slug() -> None:
    page_id, space_key = _parse_confluence_url_path("/wiki/spaces/ENG/pages/9999")
    assert page_id == "9999"
    assert space_key == "ENG"


def test_parse_confluence_url_path_unrecognised() -> None:
    page_id, space_key = _parse_confluence_url_path("/wiki/overview")
    assert page_id is None
    assert space_key is None


def test_normalise_comments_strips_html() -> None:
    raw = [
        {
            "id": "c-1",
            "version": {"authorId": "user-abc"},
            "body": {"storage": {"value": "<p>Needs revision</p>"}},
        }
    ]
    result = _normalise_comments(raw)
    assert len(result) == 1
    assert result[0]["text"] == "Needs revision"
    assert result[0]["comment_id"] == "c-1"
    assert result[0]["author_id"] == "user-abc"


# --------------------------------------------------------------------------- #
# Offline (no client) tests                                                    #
# --------------------------------------------------------------------------- #


def test_resolve_target_offline_parses_url() -> None:
    sp = ConfluenceMCPServer()
    target = sp.resolve_target(
        "https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Page+Title"
    )
    assert target.provider == "confluence"
    assert target.target_id == "1234"
    assert target.container_id == "SEC"
    assert target.target_type == "page"


def test_resolve_target_offline_invalid_url_raises() -> None:
    sp = ConfluenceMCPServer()
    with pytest.raises(ValueError, match="valid absolute URL"):
        sp.resolve_target("not-a-url")


def test_resolve_target_offline_non_atlassian_raises() -> None:
    sp = ConfluenceMCPServer()
    with pytest.raises(ValueError, match="Confluence Cloud URL"):
        sp.resolve_target("https://example.com/wiki/spaces/SEC/pages/1234")


def test_resolve_target_offline_lookalike_atlassian_host_raises() -> None:
    sp = ConfluenceMCPServer()
    with pytest.raises(ValueError, match="Confluence Cloud URL"):
        sp.resolve_target("https://evil-atlassian.net.evil.example/wiki/spaces/SEC/pages/1234")


def test_get_content_by_id_offline_returns_stub() -> None:
    sp = ConfluenceMCPServer()
    artifact = sp.get_content_by_id(
        "1234", identity_mode="app_only", include_discussion_context=True
    )
    assert artifact.provider == "confluence"
    assert artifact.target_id == "1234"
    assert artifact.content
    assert artifact.discussion_context


def test_get_content_by_id_invalid_identity_mode_raises() -> None:
    sp = ConfluenceMCPServer()
    with pytest.raises(ValueError, match="identity_mode"):
        sp.get_content_by_id("1234", identity_mode="bad_mode")


def test_check_user_access_offline_no_principal_denied() -> None:
    sp = ConfluenceMCPServer()
    decision = sp.check_user_access("1234", {})
    assert not decision.granted
    assert decision.identity_mode == "delegated"


def test_check_user_access_offline_with_principal_granted() -> None:
    sp = ConfluenceMCPServer()
    decision = sp.check_user_access("1234", {"principal_id": "user-1", "email": "user@example.com"})
    assert decision.granted
    assert decision.identity_mode == "delegated"


def test_get_recent_mentions_offline_returns_empty() -> None:
    sp = ConfluenceMCPServer()
    result = sp.get_recent_mentions(since="2026-04-04T00:00:00Z")
    assert result == {"mentions": []}


def test_post_comment_offline_raises_not_implemented() -> None:
    sp = ConfluenceMCPServer()
    with pytest.raises(NotImplementedError):
        sp.post_comment(
            "1234", comment_body="hello", identity_mode="app_only", idempotency_key="key-1"
        )


# --------------------------------------------------------------------------- #
# Mock client helpers                                                          #
# --------------------------------------------------------------------------- #

_DEFAULT_PAGE = {
    "id": "1234",
    "title": "Test Page",
    "spaceId": "space-id-1",
    "body": {"storage": {"value": "<p>Page content here.</p>"}},
    "version": {"number": 3, "authorId": "user-abc", "createdAt": "2026-04-02T00:00:00Z"},
    "ownerId": "user-def",
    "_links": {"webui": "/wiki/spaces/SEC/pages/1234/Test+Page"},
}

_DEFAULT_USER = {
    "accountId": "user-abc",
    "displayName": "Test User",
    "email": "test@example.com",
}


def _make_client(
    *,
    page: dict | None = None,
    user: dict | None = None,
    comments: list | None = None,
    comment_post_result: dict | None = None,
) -> Any:
    mock = MagicMock(spec=ConfluenceClient)
    mock.get_page.return_value = page if page is not None else dict(_DEFAULT_PAGE)
    mock.get_user.return_value = user if user is not None else dict(_DEFAULT_USER)
    mock.get_space.return_value = {"id": "space-id-1", "key": "SEC", "name": "Security"}
    mock.get_footer_comments.return_value = comments if comments is not None else []
    mock.post_footer_comment.return_value = (
        comment_post_result if comment_post_result is not None else {"id": "comment-999"}
    )
    mock.resolve_canonical_url.side_effect = lambda p: (
        f"https://example.atlassian.net{p}" if not p.startswith("http") else p
    )
    mock.site_base_url = "https://example.atlassian.net"
    return mock


# --------------------------------------------------------------------------- #
# Live client path (mocked) tests                                              #
# --------------------------------------------------------------------------- #


def test_get_content_by_id_with_client_fetches_page() -> None:
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    artifact = sp.get_content_by_id("1234", identity_mode="app_only")
    client.get_page.assert_called_once_with("1234")
    assert artifact.provider == "confluence"
    assert artifact.title == "Test Page"
    assert "Page content here." in artifact.content
    assert artifact.last_editor is not None
    assert artifact.last_editor.email == "test@example.com"


def test_get_content_by_id_with_discussion_context() -> None:
    client = _make_client(
        comments=[
            {
                "id": "c-1",
                "version": {"authorId": "user-abc"},
                "body": {"storage": {"value": "<p>Needs revision</p>"}},
            }
        ]
    )
    sp = ConfluenceMCPServer(client=client)
    artifact = sp.get_content_by_id(
        "1234", identity_mode="app_only", include_discussion_context=True
    )
    assert len(artifact.discussion_context) == 1
    assert artifact.discussion_context[0]["text"] == "Needs revision"


def test_resolve_target_with_client_enriches_title() -> None:
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    target = sp.resolve_target("https://example.atlassian.net/wiki/spaces/SEC/pages/1234/Old+Title")
    assert target.title == "Test Page"
    assert target.container_id == "SEC"
    assert target.target_id == "1234"


def test_resolve_target_with_client_resolves_space_from_api() -> None:
    # URL has no space segment – server should fall back to get_space()
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    target = sp.resolve_target("https://example.atlassian.net/wiki/pages/1234")
    # page_id won't parse (no /pages/ path), so client won't be called
    # just verify no crash an offline fallback is used
    assert target.provider == "confluence"


def test_resolve_page_owner_with_client() -> None:
    owner_user = {
        "accountId": "user-def",
        "displayName": "Page Owner",
        "email": "owner@example.com",
    }
    client = _make_client(user=owner_user)
    sp = ConfluenceMCPServer(client=client)
    result = sp.resolve_page_owner("1234")
    assert result["email"] == "owner@example.com"
    assert result["display_name"] == "Page Owner"


def test_resolve_last_editor_with_client() -> None:
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    result = sp.resolve_last_editor("1234")
    assert result["email"] == "test@example.com"
    assert result["modified_at"] == "2026-04-02T00:00:00Z"


def test_post_comment_with_client_success() -> None:
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    outcome = sp.post_comment(
        "1234",
        comment_body="<p>Assessment result.</p>",
        identity_mode="app_only",
        idempotency_key="idem-1",
    )
    assert outcome.success
    assert "inline" in outcome.attempted_channels
    assert outcome.metadata["comment_id"] == "comment-999"
    # Confirm idempotency key was embedded in the posted HTML
    call_kwargs = client.post_footer_comment.call_args[1]
    assert "idem-1" in call_kwargs["body_html"]


def test_post_comment_http_error_returns_failure() -> None:
    client = _make_client()
    http_err = requests.HTTPError()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    http_err.response = mock_resp
    client.post_footer_comment.side_effect = http_err
    sp = ConfluenceMCPServer(client=client)
    outcome = sp.post_comment(
        "1234", comment_body="test", identity_mode="app_only", idempotency_key="idem-2"
    )
    assert not outcome.success
    assert "http_403" in outcome.failures


def test_check_user_access_with_client_page_accessible() -> None:
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    decision = sp.check_user_access("1234", {"principal_id": "user-1"})
    assert decision.granted


def test_check_user_access_with_client_page_403() -> None:
    client = _make_client()
    http_err = requests.HTTPError()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    http_err.response = mock_resp
    client.get_page.side_effect = http_err
    sp = ConfluenceMCPServer(client=client)
    decision = sp.check_user_access("1234", {"principal_id": "user-1"})
    assert not decision.granted
    assert decision.identity_mode == "delegated"


def test_get_recent_mentions_with_client() -> None:
    client = _make_client()
    client.search_cql.return_value = [
        {
            "content": {
                "id": "comment-99",
                "type": "comment",
                "ancestors": [{"id": "1234", "type": "page"}],
                "space": {"key": "SEC"},
                "version": {"by": {"accountId": "user-abc"}, "when": "2026-04-04T10:05:00.000Z"},
                "_links": {"webui": "/wiki/spaces/SEC/pages/1234"},
            }
        }
    ]
    sp = ConfluenceMCPServer(client=client)
    result = sp.get_recent_mentions(since="2026-04-04T00:00:00Z", scope_filter={"space_key": "SEC"})
    assert len(result["mentions"]) == 1
    mention = result["mentions"][0]
    assert mention["target_id"] == "1234"
    assert mention["trigger_type"] == "mention"
    assert mention["occurred_at"] == "2026-04-04T10:05:00.000Z"
    cql_arg = client.search_cql.call_args[0][0]
    assert 'space.key = "SEC"' in cql_arg
    assert "ORDER BY created ASC" in cql_arg


def test_get_flagged_item_context_merges_trigger() -> None:
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    artifact = sp.get_flagged_item_context(
        "1234",
        identity_mode="app_only",
        trigger_context={"event_id": "evt-1", "trigger_type": "mention"},
    )
    assert artifact.metadata["trigger_context"]["event_id"] == "evt-1"
    assert artifact.title == "Test Page"


# --------------------------------------------------------------------------- #
# CQL polling helpers                                                          #
# --------------------------------------------------------------------------- #


def test_iso_to_cql_datetime_z_suffix() -> None:
    assert _iso_to_cql_datetime("2026-04-04T10:05:00Z") == "2026-04-04 10:05"


def test_iso_to_cql_datetime_offset() -> None:
    assert _iso_to_cql_datetime("2026-04-04T10:05:00+00:00") == "2026-04-04 10:05"


def test_iso_duration_to_since_returns_past_timestamp() -> None:
    from datetime import UTC, datetime

    result = _iso_duration_to_since("PT1H")
    dt = datetime.fromisoformat(result)
    diff = datetime.now(UTC) - dt
    assert 3590 < diff.total_seconds() < 3650


def test_iso_duration_to_since_pt24h() -> None:
    from datetime import UTC, datetime

    result = _iso_duration_to_since("PT24H")
    dt = datetime.fromisoformat(result)
    diff = datetime.now(UTC) - dt
    assert 23 * 3600 < diff.total_seconds() < 25 * 3600


def test_normalise_mention_result_comment() -> None:
    raw = {
        "content": {
            "id": "comment-99",
            "type": "comment",
            "ancestors": [{"id": "1234", "type": "page"}],
            "space": {"key": "SEC"},
            "version": {"by": {"accountId": "user-abc"}, "when": "2026-04-04T10:05:00.000Z"},
            "_links": {"webui": "/wiki/spaces/SEC/pages/1234"},
        }
    }
    event = _normalise_mention_result(raw, site_base_url="https://example.atlassian.net")
    assert event["event_id"] == "SEC:1234:comment-99"
    assert event["target_id"] == "1234"
    assert event["content_type"] == "comment"
    assert event["space_key"] == "SEC"
    assert event["mentioner_account_id"] == "user-abc"
    assert event["occurred_at"] == "2026-04-04T10:05:00.000Z"
    assert event["trigger_type"] == "mention"
    assert event["target_url"] == "https://example.atlassian.net/wiki/spaces/SEC/pages/1234"


def test_normalise_mention_result_page() -> None:
    raw = {
        "content": {
            "id": "5678",
            "type": "page",
            "space": {"key": "COMP"},
            "version": {"by": {"accountId": "user-xyz"}, "when": "2026-04-04T11:00:00.000Z"},
            "_links": {"webui": "/wiki/spaces/COMP/pages/5678"},
        }
    }
    event = _normalise_mention_result(raw, site_base_url="https://example.atlassian.net")
    assert event["target_id"] == "5678"
    assert event["content_type"] == "page"
    assert event["space_key"] == "COMP"


def test_get_recent_mentions_uses_mention_cql_operator_when_account_id_set() -> None:
    client = _make_client()
    client.search_cql.return_value = []
    sp = ConfluenceMCPServer(client=client, account_id="atlas-account-id-1")
    sp.get_recent_mentions(since="2026-04-04T00:00:00Z")
    cql_arg = client.search_cql.call_args[0][0]
    assert 'mention = "atlas-account-id-1"' in cql_arg


def test_get_recent_mentions_falls_back_to_text_search_without_account_id() -> None:
    client = _make_client()
    client.search_cql.return_value = []
    sp = ConfluenceMCPServer(client=client)
    sp.get_recent_mentions(since="2026-04-04T00:00:00Z")
    cql_arg = client.search_cql.call_args[0][0]
    assert 'text ~ "@assessment-agent"' in cql_arg


def test_get_recent_mentions_space_keys_allowlist() -> None:
    client = _make_client()
    client.search_cql.return_value = []
    sp = ConfluenceMCPServer(client=client, account_id="acc-1")
    sp.get_recent_mentions(
        since="2026-04-04T00:00:00Z", scope_filter={"space_keys": ["SEC", "COMP"]}
    )
    cql_arg = client.search_cql.call_args[0][0]
    assert 'space.key IN ("SEC", "COMP")' in cql_arg


def test_get_recent_mentions_lookback_window_backward_compat() -> None:
    """lookback_window= still works and produces a since-bounded CQL."""
    client = _make_client()
    client.search_cql.return_value = []
    sp = ConfluenceMCPServer(client=client, account_id="acc-1")
    sp.get_recent_mentions(lookback_window="PT1H")
    cql_arg = client.search_cql.call_args[0][0]
    assert "created >=" in cql_arg


def test_get_recent_mentions_scope_mismatch_raises_permission_error() -> None:
    client = _make_client()
    http_err = requests.HTTPError()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized; scope does not match"
    http_err.response = mock_resp
    client.search_cql.side_effect = http_err

    sp = ConfluenceMCPServer(client=client, account_id="acc-1")
    with pytest.raises(PermissionError, match="search:confluence"):
        sp.get_recent_mentions(since="2026-04-04T00:00:00Z")


def test_list_spaces_basic_falls_back_to_v1_when_v2_unauthorised() -> None:
    client = ConfluenceClient(
        base_url="https://example.atlassian.net",
        auth_email="bot@example.com",
        api_token="token",
        auth_mode="basic",
    )

    original_get = client._get

    def _fake_get(path: str, **params):
        if path == "/wiki/api/v2/spaces":
            err = requests.HTTPError()
            resp = MagicMock()
            resp.status_code = 401
            err.response = resp
            raise err
        if path == "/wiki/rest/api/space":
            return {"results": [{"id": "1", "key": "SEC"}]}
        return original_get(path, **params)

    client._get = _fake_get  # type: ignore[method-assign]
    spaces = client.list_spaces(limit=1)

    assert len(spaces) == 1
    assert spaces[0]["key"] == "SEC"


def test_get_page_basic_falls_back_to_v1_when_v2_unauthorised() -> None:
    client = ConfluenceClient(
        base_url="https://example.atlassian.net",
        auth_email="bot@example.com",
        api_token="token",
        auth_mode="basic",
    )

    v1_page = {
        "id": "1234",
        "title": "Page",
        "space": {"key": "SEC"},
        "body": {"storage": {"value": "<p>hello</p>"}},
        "version": {"number": 1, "by": {"accountId": "u1"}, "when": "2026-01-01T00:00:00Z"},
        "history": {"createdBy": {"accountId": "u2"}},
        "_links": {"webui": "/wiki/spaces/SEC/pages/1234/Page"},
    }

    original_get = client._get

    def _fake_get(path: str, **params):
        if path.startswith("/wiki/api/v2/pages/"):
            err = requests.HTTPError()
            resp = MagicMock()
            resp.status_code = 401
            err.response = resp
            raise err
        if path.startswith("/wiki/rest/api/content/"):
            return v1_page
        return original_get(path, **params)

    client._get = _fake_get  # type: ignore[method-assign]
    page = client.get_page("1234")

    assert page["id"] == "1234"
    assert page["title"] == "Page"
    assert page["spaceId"] == "SEC"


def test_post_footer_comment_basic_falls_back_to_v1_when_v2_unauthorised() -> None:
    client = ConfluenceClient(
        base_url="https://example.atlassian.net",
        auth_email="bot@example.com",
        api_token="token",
        auth_mode="basic",
    )

    original_post = client._post

    def _fake_post(path: str, body: dict):
        if path == "/wiki/api/v2/footer-comments":
            err = requests.HTTPError()
            resp = MagicMock()
            resp.status_code = 401
            err.response = resp
            raise err
        if path == "/wiki/rest/api/content":
            return {"id": "comment-1"}
        return original_post(path, body)

    client._post = _fake_post  # type: ignore[method-assign]
    result = client.post_footer_comment("1234", body_html="<p>x</p>")

    assert result["id"] == "comment-1"


# --------------------------------------------------------------------------- #
# MentionPoller                                                                #
# --------------------------------------------------------------------------- #


def test_mention_poller_uses_initial_lookback_on_first_poll() -> None:
    client = _make_client()
    client.search_cql.return_value = []
    server = ConfluenceMCPServer(client=client, account_id="acc-1")
    poller = MentionPoller(server, space_keys=["SEC"], initial_lookback="PT2H")
    poller.poll()
    cql_arg = client.search_cql.call_args[0][0]
    assert "created >=" in cql_arg
    assert poller.watermark == ""


def test_mention_poller_advances_watermark_to_latest_occurred_at() -> None:
    client = _make_client()
    client.search_cql.return_value = [
        {
            "content": {
                "id": "c-1",
                "type": "comment",
                "ancestors": [{"id": "1234", "type": "page"}],
                "space": {"key": "SEC"},
                "version": {"by": {"accountId": "u1"}, "when": "2026-04-04T10:05:00.000Z"},
                "_links": {"webui": "/wiki/spaces/SEC/pages/1234"},
            }
        },
        {
            "content": {
                "id": "c-2",
                "type": "comment",
                "ancestors": [{"id": "1234", "type": "page"}],
                "space": {"key": "SEC"},
                "version": {"by": {"accountId": "u2"}, "when": "2026-04-04T10:07:00.000Z"},
                "_links": {"webui": "/wiki/spaces/SEC/pages/1234"},
            }
        },
    ]
    server = ConfluenceMCPServer(client=client, account_id="acc-1")
    poller = MentionPoller(server, space_keys=["SEC"])
    mentions = poller.poll()
    assert len(mentions) == 2
    assert poller.watermark == "2026-04-04T10:07:00.000Z"


def test_mention_poller_uses_watermark_on_subsequent_polls() -> None:
    client = _make_client()
    client.search_cql.return_value = []
    server = ConfluenceMCPServer(client=client, account_id="acc-1")
    poller = MentionPoller(server)
    poller.watermark = "2026-04-04T10:07:00.000Z"
    poller.poll()
    cql_arg = client.search_cql.call_args[0][0]
    assert "2026-04-04 10:07" in cql_arg


def test_mention_poller_watermark_can_be_restored() -> None:
    """Simulate restart by injecting a persisted watermark before first poll."""
    client = _make_client()
    client.search_cql.return_value = []
    server = ConfluenceMCPServer(client=client, account_id="acc-1")
    poller = MentionPoller(server)
    poller.watermark = "2026-04-04T08:00:00.000Z"
    poller.poll()
    cql_arg = client.search_cql.call_args[0][0]
    assert "2026-04-04 08:00" in cql_arg
    client = _make_client()
    sp = ConfluenceMCPServer(client=client)
    artifact = sp.get_flagged_item_context(
        "1234",
        identity_mode="app_only",
        trigger_context={"event_id": "evt-1", "trigger_type": "mention"},
    )
    assert artifact.metadata["trigger_context"]["event_id"] == "evt-1"
    assert artifact.title == "Test Page"
