"""SharePoint MCP implementation and testing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from runtime.assessment_orchestration.mcp.sharepoint import (
    SharePointClient,
    SharePointMCPServer,
    _normalise_comments,
    _parse_sharepoint_url,
    _strip_html,
    _url_fallback_id,
)

# --------------------------------------------------------------------------- #
# Unit helpers                                                                 #
# --------------------------------------------------------------------------- #


def test_strip_html_basic() -> None:
    assert _strip_html("<p>Hello <strong>world</strong></p>") == "Hello\nworld"


def test_strip_html_empty() -> None:
    assert _strip_html("") == ""


def test_parse_sharepoint_url_standard() -> None:
    tenant, site_id, item_id = _parse_sharepoint_url(
        "https://tenant.sharepoint.com/sites/sec/SitePages/page.aspx?id=abc-123"
    )
    assert tenant == "tenant"
    assert site_id == "sec"
    assert item_id == "abc-123"


def test_parse_sharepoint_url_with_query_id() -> None:
    tenant, site_id, item_id = _parse_sharepoint_url(
        "https://tenant.sharepoint.com/sites/sec/Shared%20Documents/file.docx?id=xyz"
    )
    assert tenant == "tenant"
    assert item_id == "xyz"


def test_parse_sharepoint_url_non_sharepoint_returns_none() -> None:
    tenant, site_id, item_id = _parse_sharepoint_url("https://example.com/some/page")
    assert tenant is None


def test_url_fallback_id_deterministic() -> None:
    url = "https://tenant.sharepoint.com/sites/sec/page"
    id1 = _url_fallback_id(url)
    id2 = _url_fallback_id(url)
    assert id1 == id2
    assert len(id1) == 24


def test_normalise_comments_extracts_text() -> None:
    raw = [
        {
            "id": "c-1",
            "from": {"user": {"id": "user-abc"}},
            "body": {"content": "Comment text here"},
        }
    ]
    result = _normalise_comments(raw)
    assert len(result) == 1
    assert result[0]["text"] == "Comment text here"
    assert result[0]["author_id"] == "user-abc"


# --------------------------------------------------------------------------- #
# Offline (no client) tests                                                    #
# --------------------------------------------------------------------------- #


def test_resolve_target_offline_parses_url() -> None:
    sp = SharePointMCPServer()
    target = sp.resolve_target(
        "https://tenant.sharepoint.com/sites/sec/SitePages/page.aspx?id=abc-123"
    )
    assert target.provider == "sharepoint"
    assert target.target_id == "abc-123"
    assert target.container_id == "sec"
    assert target.target_type == "page"


def test_resolve_target_offline_invalid_url_raises() -> None:
    sp = SharePointMCPServer()
    with pytest.raises(ValueError, match="valid absolute URL"):
        sp.resolve_target("not-a-url")


def test_resolve_target_offline_non_sharepoint_raises() -> None:
    sp = SharePointMCPServer()
    with pytest.raises(ValueError, match="SharePoint URL"):
        sp.resolve_target("https://example.com/sites/sec/page")


def test_get_content_by_id_offline_returns_stub() -> None:
    sp = SharePointMCPServer()
    artifact = sp.get_content_by_id(
        "abc-123", identity_mode="app_only", include_discussion_context=True
    )
    assert artifact.provider == "sharepoint"
    assert artifact.target_id == "abc-123"
    assert artifact.content
    assert artifact.discussion_context


def test_get_content_by_id_invalid_identity_mode_raises() -> None:
    sp = SharePointMCPServer()
    with pytest.raises(ValueError, match="identity_mode"):
        sp.get_content_by_id("abc-123", identity_mode="bad_mode")


def test_check_user_access_offline_no_principal_denied() -> None:
    sp = SharePointMCPServer()
    decision = sp.check_user_access("abc-123", {})
    assert not decision.granted


def test_check_user_access_offline_with_principal_granted() -> None:
    sp = SharePointMCPServer()
    decision = sp.check_user_access(
        "abc-123", {"principal_id": "user-1", "email": "user@example.com"}
    )
    assert decision.granted


def test_get_recent_mentions_offline_returns_empty() -> None:
    sp = SharePointMCPServer()
    result = sp.get_recent_mentions(lookback_window="PT1H")
    assert result == {"mentions": []}


def test_post_comment_offline_raises_not_implemented() -> None:
    sp = SharePointMCPServer()
    with pytest.raises(NotImplementedError):
        sp.post_comment(
            "abc-123", comment_body="hello", identity_mode="app_only", idempotency_key="key-1"
        )


# --------------------------------------------------------------------------- #
# Mock client helpers                                                          #
# --------------------------------------------------------------------------- #

_DEFAULT_ITEM = {
    "id": "abc-123",
    "name": "Policy Document",
    "webUrl": "https://tenant.sharepoint.com/sites/sec/Shared%20Documents/policy.docx",
    "lastModifiedDateTime": "2026-04-02T10:00:00Z",
    "createdBy": {
        "user": {"id": "user-owner", "displayName": "Owner User", "mail": "owner@example.com"}
    },
    "lastModifiedBy": {
        "user": {"id": "user-editor", "displayName": "Editor User", "mail": "editor@example.com"}
    },
}

_DEFAULT_USER = {
    "id": "user-editor",
    "displayName": "Editor User",
    "mail": "editor@example.com",
    "userPrincipalName": "editor@example.onmicrosoft.com",
}


def _make_sharepoint_client(
    *,
    item: dict | None = None,
    user: dict | None = None,
    content: str | None = None,
    comments: list | None = None,
) -> SharePointClient:
    mock = MagicMock(spec=SharePointClient)
    mock._tenant = "tenant"
    mock._site_id = "site-sec"
    mock.get_item.return_value = item if item is not None else dict(_DEFAULT_ITEM)
    mock.get_user.return_value = user if user is not None else dict(_DEFAULT_USER)
    mock.get_item_content.return_value = content if content is not None else "Item content here."
    mock._get.return_value = {"value": comments if comments is not None else []}
    mock.post_comment.return_value = {"id": "comment-999"}
    return mock


# --------------------------------------------------------------------------- #
# Live client path (mocked) tests                                              #
# --------------------------------------------------------------------------- #


def test_get_content_by_id_with_client_fetches_item() -> None:
    client = _make_sharepoint_client()
    sp = SharePointMCPServer(client=client)
    artifact = sp.get_content_by_id("abc-123", identity_mode="app_only")
    client.get_item.assert_called_once_with("abc-123")
    assert artifact.provider == "sharepoint"
    assert artifact.title == "Policy Document"
    assert "Item content here." in artifact.content
    assert artifact.last_editor is not None
    assert artifact.last_editor.email == "editor@example.com"


def test_get_content_by_id_with_discussion_context() -> None:
    client = _make_sharepoint_client(
        comments=[
            {
                "id": "c-1",
                "from": {"user": {"id": "user-abc"}},
                "body": {"content": "Needs revision"},
            }
        ]
    )
    sp = SharePointMCPServer(client=client)
    artifact = sp.get_content_by_id(
        "abc-123", identity_mode="app_only", include_discussion_context=True
    )
    assert len(artifact.discussion_context) == 1
    assert artifact.discussion_context[0]["text"] == "Needs revision"


def test_resolve_target_with_client_enriches_title() -> None:
    client = _make_sharepoint_client()
    sp = SharePointMCPServer(client=client)
    target = sp.resolve_target(
        "https://tenant.sharepoint.com/sites/sec/SitePages/OldTitle?id=abc-123"
    )
    assert target.title == "Policy Document"
    assert target.container_id == "sec"
    assert target.target_id == "abc-123"


def test_resolve_page_owner_with_client() -> None:
    owner_user = {"id": "user-owner", "displayName": "Document Owner", "mail": "owner@example.com"}
    client = _make_sharepoint_client(user=owner_user)
    sp = SharePointMCPServer(client=client)
    result = sp.resolve_page_owner("abc-123")
    assert result["email"] == "owner@example.com"
    assert result["display_name"] == "Document Owner"


def test_resolve_last_editor_with_client() -> None:
    client = _make_sharepoint_client()
    sp = SharePointMCPServer(client=client)
    result = sp.resolve_last_editor("abc-123")
    assert result["email"] == "editor@example.com"
    assert result["modified_at"] == "2026-04-02T10:00:00Z"


def test_post_comment_with_client_success() -> None:
    client = _make_sharepoint_client()
    sp = SharePointMCPServer(client=client)
    outcome = sp.post_comment(
        "abc-123",
        comment_body="Assessment result",
        identity_mode="app_only",
        idempotency_key="idem-1",
    )
    assert outcome.success
    assert "inline" in outcome.attempted_channels
    assert outcome.metadata["comment_id"] == "comment-999"
    call_kwargs = client.post_comment.call_args[1]
    assert "idem-1" in call_kwargs["body"]


def test_post_comment_http_error_returns_failure() -> None:
    client = _make_sharepoint_client()
    http_err = requests.HTTPError()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    http_err.response = mock_resp
    client.post_comment.side_effect = http_err
    sp = SharePointMCPServer(client=client)
    outcome = sp.post_comment(
        "abc-123", comment_body="test", identity_mode="app_only", idempotency_key="idem-2"
    )
    assert not outcome.success
    assert "http_403" in outcome.failures


def test_check_user_access_with_client_item_accessible() -> None:
    client = _make_sharepoint_client()
    sp = SharePointMCPServer(client=client)
    decision = sp.check_user_access("abc-123", {"principal_id": "user-1"})
    assert decision.granted


def test_check_user_access_with_client_item_403() -> None:
    client = _make_sharepoint_client()
    http_err = requests.HTTPError()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    http_err.response = mock_resp
    client.get_item.side_effect = http_err
    sp = SharePointMCPServer(client=client)
    decision = sp.check_user_access("abc-123", {"principal_id": "user-1"})
    assert not decision.granted


def test_get_flagged_item_context_merges_trigger() -> None:
    client = _make_sharepoint_client()
    sp = SharePointMCPServer(client=client)
    artifact = sp.get_flagged_item_context(
        "abc-123",
        identity_mode="app_only",
        trigger_context={"event_id": "evt-1", "trigger_type": "mention"},
    )
    assert artifact.metadata["trigger_context"]["event_id"] == "evt-1"
    assert artifact.title == "Policy Document"
