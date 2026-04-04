"""Live integration tests against a real Confluence Cloud instance.

Environment variables:
    CONFLUENCE_LIVE_RUN      Must be 1/true/yes/on to execute these tests
    CONFLUENCE_BASE_URL      e.g. https://your-org.atlassian.net
    CONFLUENCE_AUTH_MODE     basic (default), bearer, or oauth

If CONFLUENCE_AUTH_MODE=basic:
    CONFLUENCE_API_TOKEN     Required
    CONFLUENCE_AUTH_EMAIL    Required

If CONFLUENCE_AUTH_MODE=bearer:
    CONFLUENCE_API_TOKEN     Required
    CONFLUENCE_CLOUD_ID      Optional if CONFLUENCE_BASE_URL is set
                            (falls back to {base_url}/_edge/tenant_info lookup)

If CONFLUENCE_AUTH_MODE=oauth:
    CONFLUENCE_OAUTH_ACCESS_TOKEN  Required
    CONFLUENCE_CLOUD_ID            Optional if CONFLUENCE_BASE_URL is set
                                   (falls back to {base_url}/_edge/tenant_info lookup)

Skip tag: pytest -m confluence_live
To run: pytest tests/integration/test_confluence_live.py -v -m confluence_live
"""
from __future__ import annotations

import os
import time

import pytest
import requests

from runtime.assessment_orchestration.mcp.confluence import ConfluenceClient, ConfluenceMCPServer


pytestmark = pytest.mark.confluence_live


# --------------------------------------------------------------------------- #
# Session-scoped fixtures                                                      #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def _require_live_opt_in() -> None:
    run_live = (os.getenv("CONFLUENCE_LIVE_RUN") or "").strip().lower()
    if run_live not in {"1", "true", "yes", "on"}:
        pytest.skip("Set CONFLUENCE_LIVE_RUN=1 to execute live Confluence integration tests")


@pytest.fixture(scope="session")
def base_url(_require_live_opt_in: None) -> str:
    url = (os.getenv("CONFLUENCE_BASE_URL") or "").strip().rstrip("/")
    if not url:
        pytest.skip("CONFLUENCE_BASE_URL not set")
    return url


@pytest.fixture(scope="session")
def auth_mode() -> str:
    mode = (os.getenv("CONFLUENCE_AUTH_MODE") or "basic").strip().lower()
    if mode not in {"basic", "bearer", "oauth"}:
        pytest.skip("CONFLUENCE_AUTH_MODE must be basic, bearer, or oauth")
    return mode


@pytest.fixture(scope="session")
def auth_email(auth_mode: str) -> str:
    if auth_mode != "basic":
        return ""
    email = (os.getenv("CONFLUENCE_AUTH_EMAIL") or "").strip()
    if not email:
        pytest.skip("CONFLUENCE_AUTH_EMAIL not set")
    return email


@pytest.fixture(scope="session")
def api_token() -> str:
    mode = (os.getenv("CONFLUENCE_AUTH_MODE") or "basic").strip().lower()
    if mode == "oauth":
        return ""
    token = (os.getenv("CONFLUENCE_API_TOKEN") or "").strip()
    if not token:
        pytest.skip("CONFLUENCE_API_TOKEN not set")
    return token


@pytest.fixture(scope="session")
def oauth_access_token(auth_mode: str) -> str:
    if auth_mode != "oauth":
        return ""
    token = (os.getenv("CONFLUENCE_OAUTH_ACCESS_TOKEN") or "").strip()
    return token


@pytest.fixture(scope="session")
def oauth_client_id(auth_mode: str) -> str:
    if auth_mode != "oauth":
        return ""
    return (os.getenv("CONFLUENCE_OAUTH_CLIENT_ID") or "").strip()


@pytest.fixture(scope="session")
def oauth_client_secret(auth_mode: str) -> str:
    if auth_mode != "oauth":
        return ""
    return (os.getenv("CONFLUENCE_OAUTH_CLIENT_SECRET") or "").strip()


@pytest.fixture(scope="session")
def oauth_token_url(auth_mode: str) -> str:
    if auth_mode != "oauth":
        return ""
    return (os.getenv("CONFLUENCE_OAUTH_TOKEN_URL") or "").strip() or "https://auth.atlassian.com/oauth/token"


@pytest.fixture(scope="session")
def oauth_scope(auth_mode: str) -> str | None:
    if auth_mode != "oauth":
        return None
    value = (os.getenv("CONFLUENCE_OAUTH_SCOPE") or "").strip()
    return value or None


@pytest.fixture(scope="session")
def oauth_audience(auth_mode: str) -> str | None:
    if auth_mode != "oauth":
        return None
    value = (os.getenv("CONFLUENCE_OAUTH_AUDIENCE") or "").strip()
    return value or None


@pytest.fixture(scope="session")
def cloud_id(base_url: str, auth_mode: str) -> str:
    if auth_mode not in {"bearer", "oauth"}:
        return ""
    from_env = (os.getenv("CONFLUENCE_CLOUD_ID") or "").strip()
    if from_env:
        return from_env
    try:
        resp = requests.get(f"{base_url}/_edge/tenant_info", timeout=10)
        resp.raise_for_status()
        value = (resp.json() or {}).get("cloudId") or ""
        if value:
            return str(value)
    except requests.RequestException as exc:
        pytest.skip(f"Could not resolve CONFLUENCE_CLOUD_ID from tenant_info: {exc}")
    pytest.skip("CONFLUENCE_CLOUD_ID not set and tenant_info did not return cloudId")


@pytest.fixture(scope="session")
def live_client(
    base_url: str,
    auth_mode: str,
    auth_email: str,
    api_token: str,
    oauth_access_token: str,
    oauth_client_id: str,
    oauth_client_secret: str,
    oauth_token_url: str,
    oauth_scope: str | None,
    oauth_audience: str | None,
    cloud_id: str,
) -> ConfluenceClient:
    if auth_mode == "bearer":
        return ConfluenceClient(
            base_url=base_url,
            api_token=api_token,
            auth_mode="bearer",
            cloud_id=cloud_id,
        )
    if auth_mode == "oauth":
        if not oauth_access_token and not (oauth_client_id and oauth_client_secret):
            pytest.skip(
                "oauth mode requires CONFLUENCE_OAUTH_ACCESS_TOKEN or "
                "CONFLUENCE_OAUTH_CLIENT_ID + CONFLUENCE_OAUTH_CLIENT_SECRET"
            )
        return ConfluenceClient(
            base_url=base_url,
            oauth_access_token=oauth_access_token or None,
            oauth_client_id=oauth_client_id or None,
            oauth_client_secret=oauth_client_secret or None,
            oauth_token_url=oauth_token_url,
            oauth_scope=oauth_scope,
            oauth_audience=oauth_audience,
            auth_mode="oauth",
            cloud_id=cloud_id,
        )
    return ConfluenceClient(
        base_url=base_url,
        auth_email=auth_email,
        api_token=api_token,
        auth_mode="basic",
    )


@pytest.fixture(scope="session")
def live_mcp(live_client: ConfluenceClient, service_account_id: str) -> ConfluenceMCPServer:
    return ConfluenceMCPServer(client=live_client, account_id=service_account_id)


@pytest.fixture(scope="session")
def service_account_id(live_client: ConfluenceClient) -> str:
    """Extract the service account's Atlassian account ID for mention testing."""
    user_info = live_client.get_current_user()
    account_id = user_info.get("accountId") or ""
    if not account_id:
        pytest.skip(f"Could not extract accountId from get_current_user(): {user_info}")
    return account_id


@pytest.fixture(scope="session")
def test_space_key() -> str | None:
    """Return the test space key if provided via TEST_CONFLUENCE_SPACE_KEY env var."""
    return (os.getenv("TEST_CONFLUENCE_SPACE_KEY") or "").strip() or None


@pytest.fixture(scope="session")
def test_page_id() -> str | None:
    """Return the test page ID if provided via TEST_CONFLUENCE_PAGE_ID env var."""
    return (os.getenv("TEST_CONFLUENCE_PAGE_ID") or "").strip() or None


@pytest.fixture(scope="session")
def first_space(live_client: ConfluenceClient) -> dict:
    """Return the first accessible space, or skip if none exist."""
    spaces = live_client.list_spaces(limit=1)
    if not spaces:
        pytest.skip("No accessible Confluence spaces found for this account")
    return spaces[0]


@pytest.fixture(scope="session")
def first_page(live_client: ConfluenceClient, first_space: dict) -> dict:
    """Return the first page found via CQL, or skip if none exist."""
    space_key = first_space.get("key") or ""
    cql = f'type = "page" AND space.key = "{space_key}"' if space_key else 'type = "page"'
    results = live_client.search_cql(cql, limit=1)
    if not results:
        pytest.skip(f"No pages found in space {space_key!r}")
    return results[0]


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #

def test_list_spaces_returns_at_least_one(live_client: ConfluenceClient) -> None:
    """Confirms authentication is working and the account can access at least one space."""
    spaces = live_client.list_spaces(limit=10)
    assert isinstance(spaces, list)
    assert len(spaces) >= 1, "Expected at least one accessible Confluence space"
    space = spaces[0]
    assert "key" in space, f"Space missing 'key': {space}"
    assert "name" in space, f"Space missing 'name': {space}"


def test_cql_search_returns_results(live_client: ConfluenceClient) -> None:
    """Confirms CQL search endpoint is reachable and returns structured results."""
    results = live_client.search_cql('type = "page"', limit=5)
    assert isinstance(results, list), "search_cql should return a list"
    if results:
        # Each result has at least a title or content field
        result = results[0]
        assert "title" in result or "content" in result, f"Unexpected result shape: {result}"


def test_get_page_by_id(live_client: ConfluenceClient, first_page: dict) -> None:
    """Fetch a specific page by ID and confirm expected fields are present."""
    page_id = (
        first_page.get("id")
        or (first_page.get("content") or {}).get("id")
        or ""
    )
    if not page_id:
        pytest.skip("Could not extract page ID from CQL result")

    page = live_client.get_page(page_id)
    assert page.get("id") == page_id
    assert "title" in page, f"Page missing 'title': {page.keys()}"
    assert "version" in page, f"Page missing 'version': {page.keys()}"


def test_resolve_target_live(live_mcp: ConfluenceMCPServer, live_client: ConfluenceClient, first_page: dict) -> None:
    """resolve_target should parse a real Confluence page URL and return canonical form."""
    page_id = (
        first_page.get("id")
        or (first_page.get("content") or {}).get("id")
        or ""
    )
    if not page_id:
        pytest.skip("Could not extract page ID from CQL result")

    page = live_client.get_page(page_id)
    webui = (page.get("_links") or {}).get("webui") or ""
    if not webui:
        pytest.skip(f"Page {page_id} has no _links.webui")

    canonical_url = live_client.resolve_canonical_url(webui)
    target = live_mcp.resolve_target(canonical_url)

    assert target.provider == "confluence"
    assert target.target_id == page_id
    assert target.target_type == "page"
    assert target.canonical_url.startswith("http")
    assert target.title  # Enriched from live API


def test_get_content_by_id_live(live_mcp: ConfluenceMCPServer, first_page: dict) -> None:
    """get_content_by_id should return a populated AssessedArtifactPackage."""
    page_id = (
        first_page.get("id")
        or (first_page.get("content") or {}).get("id")
        or ""
    )
    if not page_id:
        pytest.skip("Could not extract page ID from CQL result")

    artifact = live_mcp.get_content_by_id(page_id, identity_mode="app_only")

    assert artifact.provider == "confluence"
    assert artifact.target_id == page_id
    assert artifact.title
    assert artifact.canonical_url.startswith("http")
    # Content may be empty for a blank page, but field must exist
    assert isinstance(artifact.content, str)


def test_get_recent_mentions_live(live_mcp: ConfluenceMCPServer, first_space: dict) -> None:
    """get_recent_mentions should succeed without error (results may be empty)."""
    space_key = first_space.get("key") or ""
    result = live_mcp.get_recent_mentions(
        lookback_window="PT24H",
        scope_filter={"space_key": space_key} if space_key else None,
    )
    assert "mentions" in result
    assert isinstance(result["mentions"], list)


def test_resolve_page_owner_live(live_mcp: ConfluenceMCPServer, first_page: dict) -> None:
    """resolve_page_owner should return a dict with principal_id."""
    page_id = (
        first_page.get("id")
        or (first_page.get("content") or {}).get("id")
        or ""
    )
    if not page_id:
        pytest.skip("Could not extract page ID from CQL result")

    owner = live_mcp.resolve_page_owner(page_id)
    assert isinstance(owner, dict)
    assert "principal_id" in owner, f"Missing principal_id: {owner}"
    assert "display_name" in owner, f"Missing display_name: {owner}"


def test_resolve_last_editor_live(live_mcp: ConfluenceMCPServer, first_page: dict) -> None:
    """resolve_last_editor should return a dict with principal_id and modified_at."""
    page_id = (
        first_page.get("id")
        or (first_page.get("content") or {}).get("id")
        or ""
    )
    if not page_id:
        pytest.skip("Could not extract page ID from CQL result")

    editor = live_mcp.resolve_last_editor(page_id)
    assert isinstance(editor, dict)
    assert "principal_id" in editor, f"Missing principal_id: {editor}"
    assert "modified_at" in editor, f"Missing modified_at: {editor}"




@pytest.mark.skipif(
    not all([
        os.environ.get("TEST_CONFLUENCE_SPACE_KEY"),
        os.environ.get("TEST_CONFLUENCE_PAGE_ID"),
    ]),
    reason="TEST_CONFLUENCE_SPACE_KEY and TEST_CONFLUENCE_PAGE_ID required for CQL event capture test",
)
def test_post_comment_and_capture_mention_event_live(
    live_client: ConfluenceClient,
    live_mcp: ConfluenceMCPServer,
    service_account_id: str,
    test_space_key: str | None,
    test_page_id: str | None,
) -> None:
    """Write a mention comment and capture it via get_recent_mentions polling.

    This validates the end-to-end CQL mention event path used by the trigger pipeline.
    Requires TEST_CONFLUENCE_SPACE_KEY and TEST_CONFLUENCE_PAGE_ID env vars.
    """

    space_key = test_space_key
    page_id = test_page_id
    if not space_key or not page_id:
        pytest.skip("TEST_CONFLUENCE_SPACE_KEY or TEST_CONFLUENCE_PAGE_ID not set")

    # Storage-format mention using account-id, compatible with Confluence Cloud.
    comment_body = (
        f"<p>CQL event capture test <ac:link><ri:user ri:account-id=\"{service_account_id}\"/></ac:link></p>"
    )
    comment_response = live_client.post_footer_comment(page_id, body_html=comment_body)
    assert comment_response, "post_footer_comment returned empty response"

    posted_comment_id = str(comment_response.get("id") or "")
    if not posted_comment_id:
        pytest.skip(f"Could not extract comment id from response: {comment_response}")

    mentions: list[dict] = []
    for _ in range(8):
        poll = live_mcp.get_recent_mentions(
            lookback_window="PT5M",
            scope_filter={"space_key": space_key},
        )
        mentions = poll.get("mentions", [])
        if any(str(item.get("target_id") or "") == posted_comment_id for item in mentions):
            break
        time.sleep(2)

    assert mentions, "Expected at least one mention event from CQL polling"
    assert any(str(item.get("target_id") or "") == posted_comment_id for item in mentions), (
        f"Posted comment id {posted_comment_id} not found in mention target_ids: "
        f"{[str(item.get('target_id') or '') for item in mentions[:10]]}"
    )
