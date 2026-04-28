from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests  # type: ignore[import-untyped]
from requests.auth import AuthBase, HTTPBasicAuth  # type: ignore[import-untyped]

from runtime.outbound_instrumentation import (
    InstrumentedRequestsSession,
    request_with_instrumentation,
)

from ..models import (
    AccessDecision,
    AssessedArtifactPackage,
    DeliveryOutcome,
    PersonReference,
    ResolvedTarget,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# HTML stripping                                                               #
# --------------------------------------------------------------------------- #


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML tag stripper using stdlib – no extra dependencies."""

    def __init__(self) -> None:
        """Run init."""
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Run handle data."""
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        """Run get text."""
        return "\n".join(self._parts)


def _strip_html(html: str) -> str:
    """Run strip html."""
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def _host_is_exact_or_subdomain(host: str, domain: str) -> bool:
    """Run host is exact or subdomain."""
    host_l = host.strip().lower()
    domain_l = domain.strip().lower()
    return host_l == domain_l or host_l.endswith(f".{domain_l}")


# --------------------------------------------------------------------------- #
# CQL / datetime helpers                                                       #
# --------------------------------------------------------------------------- #


def _iso_to_cql_datetime(iso_str: str) -> str:
    """Convert ISO 8601 datetime to Confluence CQL datetime format (YYYY-MM-DD HH:mm)."""
    clean = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
    except ValueError:
        return iso_str[:16].replace("T", " ")
    return dt.strftime("%Y-%m-%d %H:%M")


def _extract_mention_occurred_at(content: dict[str, Any], result: dict[str, Any]) -> str:
    """Best-effort extraction of event time from Confluence CQL result shapes."""
    version = content.get("version") or {}
    history = content.get("history") or {}
    last_updated = history.get("lastUpdated") or {}
    result_last_modified = result.get("lastModified")

    candidates: list[Any] = [
        version.get("when"),
        last_updated.get("when"),
        content.get("createdDate"),
        result.get("lastModifiedDate"),
        result_last_modified.get("when") if isinstance(result_last_modified, dict) else None,
        result_last_modified,
    ]

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _iso_duration_to_since(duration: str) -> str:
    """Convert an ISO 8601 duration string (e.g. PT1H, PT24H, P1D) to an absolute ISO timestamp."""
    upper = duration.upper()
    hours = minutes = days = 0
    m = re.search(r"(\d+)H", upper)
    if m:
        hours = int(m.group(1))
    m = re.search(r"(\d+)M(?!O)", upper)
    if m:
        minutes = int(m.group(1))
    m = re.search(r"P(\d+)D", upper)
    if m:
        days = int(m.group(1))
    delta = timedelta(hours=hours, minutes=minutes, days=days)
    if not delta:
        delta = timedelta(hours=1)
    return (datetime.now(UTC) - delta).isoformat()


def _normalise_mention_result(result: dict[str, Any], *, site_base_url: str) -> dict[str, Any]:
    """Normalise a Confluence CQL search result into a structured mention event."""
    # CQL /wiki/rest/api/search results wrap the content object
    content = result.get("content") or result
    content_id = str(content.get("id") or "")
    content_type = str(content.get("type") or "comment")
    links = content.get("_links") or {}
    webui = str(links.get("webui") or result.get("url") or "")

    # For comments the mentioning page is in ancestors; for pages it is the content itself
    ancestors = content.get("ancestors") or []
    if content_type == "comment" and ancestors:
        page_id = str(ancestors[0].get("id") or "")
    else:
        page_id = content_id

    space = content.get("space") or {}
    space_key = str(space.get("key") or "")

    version = content.get("version") or {}
    mentioner_by = version.get("by") or {}
    mentioner_account_id = str(mentioner_by.get("accountId") or "")
    occurred_at = _extract_mention_occurred_at(content, result)
    trigger_text = _strip_html(str(result.get("excerpt") or "")).strip()
    title = str(content.get("title") or "").strip()

    if webui:
        canonical_url = webui if webui.startswith("http") else f"{site_base_url}{webui}"
    else:
        canonical_url = ""

    # Some CQL responses omit ancestors for comments. Recover page/space from URL path.
    if (
        content_type == "comment"
        and canonical_url
        and ((not page_id) or (page_id == content_id) or (not space_key))
    ):
        parsed_page_id, parsed_space_key = _parse_confluence_url_path(urlparse(canonical_url).path)
        if parsed_page_id:
            page_id = parsed_page_id
        if parsed_space_key and not space_key:
            space_key = parsed_space_key

    # If the API omits event time fields, avoid dropping a valid mention event.
    if not occurred_at:
        occurred_at = datetime.now(UTC).isoformat()

    event_id = f"{space_key}:{page_id}:{content_id}"

    return {
        "event_id": event_id,
        "content_id": content_id,
        "content_type": content_type,
        "title": title,
        "target_id": page_id or content_id,
        "target_url": canonical_url,
        "space_key": space_key,
        "mentioner_account_id": mentioner_account_id,
        "occurred_at": occurred_at,
        "trigger_text": trigger_text,
        "trigger_type": "mention",
    }


# --------------------------------------------------------------------------- #
# URL helpers                                                                  #
# --------------------------------------------------------------------------- #


def _parse_confluence_url_path(path: str) -> tuple[str | None, str | None]:
    """Return (page_id, space_key) from a Confluence Cloud URL path.

    Handles:
      /wiki/spaces/{SPACE_KEY}/pages/{page_id}[/{slug}]
    """
    parts = [p for p in path.split("/") if p]
    page_id: str | None = None
    space_key: str | None = None
    try:
        if "spaces" in parts:
            idx = parts.index("spaces")
            if idx + 1 < len(parts):
                space_key = parts[idx + 1]
        if "pages" in parts:
            idx = parts.index("pages")
            if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                page_id = parts[idx + 1]
    except (ValueError, IndexError):
        pass
    return page_id, space_key


def _url_fallback_id(url: str) -> str:
    """Run url fallback id."""
    import hashlib

    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _raise_for_status_with_body(resp: requests.Response) -> None:
    """Raise HTTPError with a short response-body snippet for easier debugging."""
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        body = (resp.text or "").strip()
        if not body:
            raise
        snippet = body[:500]
        raise requests.HTTPError(
            f"{exc} | response body: {snippet}",
            response=resp,
            request=resp.request,
        ) from exc


# --------------------------------------------------------------------------- #
# Confluence REST API client                                                   #
# --------------------------------------------------------------------------- #


class ConfluenceClient:
    """Thin wrapper around the Confluence Cloud REST API (v2 primary, v1 for search/users)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str | None = None,
        oauth_access_token: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_token_url: str = "https://auth.atlassian.com/oauth/token",
        oauth_scope: str | None = None,
        oauth_audience: str | None = None,
        auth_email: str | None = None,
        auth_mode: str = "basic",
        cloud_id: str | None = None,
    ) -> None:
        """Run init."""
        self._site_base_url = base_url.rstrip("/")
        self._auth_mode = auth_mode
        if auth_mode == "basic":
            if not auth_email:
                raise ValueError("auth_email is required when auth_mode is basic")
            if not api_token:
                raise ValueError("api_token is required when auth_mode is basic")
            self._api_base_url = self._site_base_url
        elif auth_mode == "bearer":
            if not cloud_id:
                raise ValueError("cloud_id is required when auth_mode is bearer")
            if not api_token:
                raise ValueError("api_token is required when auth_mode is bearer")
            self._api_base_url = f"https://api.atlassian.com/ex/confluence/{cloud_id}"
        elif auth_mode == "oauth":
            if not cloud_id:
                raise ValueError("cloud_id is required when auth_mode is oauth")
            has_static_token = bool(oauth_access_token)
            has_client_credentials = bool(oauth_client_id and oauth_client_secret)
            if not (has_static_token or has_client_credentials):
                raise ValueError(
                    "oauth mode requires oauth_access_token or oauth_client_id + oauth_client_secret"
                )
            self._api_base_url = f"https://api.atlassian.com/ex/confluence/{cloud_id}"
        else:
            raise ValueError("auth_mode must be basic, bearer, or oauth")

        self._session = InstrumentedRequestsSession(logger=logger, system="confluence")
        # TODO: Add OAuth 2.0 support for Confluence Cloud (recommended over API tokens).
        if auth_mode == "basic":
            # Narrow optional input after validation above for static type checkers.
            basic_auth_email = auth_email
            basic_api_token = api_token
            if basic_auth_email is None:
                raise ValueError("auth_email is required when auth_mode is basic")
            if basic_api_token is None:
                raise ValueError("api_token is required when auth_mode is basic")
            self._session.auth = HTTPBasicAuth(basic_auth_email, basic_api_token)
        elif auth_mode == "bearer":
            bearer_token = api_token
            if bearer_token is None:
                raise ValueError("api_token is required when auth_mode is bearer")
            self._session.auth = _BearerTokenAuth(bearer_token)
        else:
            oauth_token = oauth_access_token
            if oauth_token:
                self._session.auth = _BearerTokenAuth(oauth_token)
            else:
                oauth_id = oauth_client_id
                oauth_secret = oauth_client_secret
                if oauth_id is None or oauth_secret is None:
                    raise ValueError(
                        "oauth_client_id and oauth_client_secret are required in oauth mode"
                    )
                self._session.auth = _OAuthClientCredentialsAuth(
                    token_url=oauth_token_url,
                    client_id=oauth_id,
                    client_secret=oauth_secret,
                    scope=oauth_scope,
                    audience=oauth_audience,
                )
        self._session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, **params: Any) -> Any:
        """Run get."""
        url = f"{self._api_base_url}{path}"
        resp = self._session.get(url, params=params)
        _raise_for_status_with_body(resp)
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        """Run post."""
        url = f"{self._api_base_url}{path}"
        resp = self._session.post(url, json=body)
        _raise_for_status_with_body(resp)
        return resp.json()

    def get_page(self, page_id: str, *, body_format: str = "storage") -> dict[str, Any]:
        """Run get page."""
        if self._auth_mode in {"bearer", "oauth"}:
            result = self._get(
                f"/wiki/rest/api/content/{page_id}",
                expand="body.storage,version,space,history,history.lastUpdated",
            )
            return self._normalise_v1_page(result)

        # Basic tokens vary by scope support across v1/v2 endpoints; prefer v2 and fall back to v1.
        try:
            result = self._get(f"/wiki/api/v2/pages/{page_id}", **{"body-format": body_format})
            return result  # type: ignore[return-value]
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (401, 403):
                raise
            result = self._get(
                f"/wiki/rest/api/content/{page_id}",
                expand="body.storage,version,space,history,history.lastUpdated",
            )
            return self._normalise_v1_page(result)

    def get_user(self, account_id: str) -> dict[str, Any]:
        """Run get user."""
        result = self._get("/wiki/rest/api/user", accountId=account_id)
        return result  # type: ignore[return-value]

    def get_space(self, space_id: str) -> dict[str, Any]:
        """Run get space."""
        if self._auth_mode in {"bearer", "oauth"}:
            result = self._get(f"/wiki/rest/api/space/{space_id}")
            return {
                "id": result.get("id") or "",
                "key": result.get("key") or "",
                "name": result.get("name") or "",
            }
        result = self._get(f"/wiki/api/v2/spaces/{space_id}")
        return result  # type: ignore[return-value]

    def get_footer_comments(self, page_id: str) -> list[dict[str, Any]]:
        """Run get footer comments."""
        if self._auth_mode in {"bearer", "oauth"}:
            result = self._get(
                f"/wiki/rest/api/content/{page_id}/child/comment",
                expand="body.storage,version",
            )
            return self._normalise_v1_comments(result.get("results", []))

        try:
            result = self._get(
                f"/wiki/api/v2/pages/{page_id}/footer-comments",
                **{"body-format": "storage"},
            )
            return result.get("results", [])  # type: ignore[return-value]
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (401, 403):
                raise
            result = self._get(
                f"/wiki/rest/api/content/{page_id}/child/comment",
                expand="body.storage,version",
            )
            return self._normalise_v1_comments(result.get("results", []))

    def get_comment(self, comment_id: str) -> dict[str, Any]:
        """Fetch a single comment by its ID."""
        # Try v2 first, then v1 for compatibility
        try:
            result = self._get(f"/wiki/api/v2/comments/{comment_id}")
            return result  # type: ignore[return-value]
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            # Fallback to v1 for any 4xx error (not just 401/403/404)
            if 400 <= status < 500:
                result = self._get(
                    f"/wiki/rest/api/content/{comment_id}", expand="body.storage,version"
                )
                return result  # type: ignore[return-value]
            raise

    def post_footer_comment(self, page_id: str, *, body_html: str) -> dict[str, Any]:
        """Run post footer comment."""
        if self._auth_mode in {"bearer", "oauth"}:
            result = self._post(
                "/wiki/rest/api/content",
                {
                    "type": "comment",
                    "container": {"id": page_id, "type": "page"},
                    "body": {"storage": {"representation": "storage", "value": body_html}},
                },
            )
            return result  # type: ignore[return-value]

        try:
            result = self._post(
                "/wiki/api/v2/footer-comments",
                {
                    "pageId": page_id,
                    "body": {"representation": "storage", "value": body_html},
                },
            )
            return result  # type: ignore[return-value]
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (401, 403):
                raise
            result = self._post(
                "/wiki/rest/api/content",
                {
                    "type": "comment",
                    "container": {"id": page_id, "type": "page"},
                    "body": {"storage": {"representation": "storage", "value": body_html}},
                },
            )
            return result  # type: ignore[return-value]

    def list_spaces(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Run list spaces."""
        if self._auth_mode in {"bearer", "oauth"}:
            result = self._get("/wiki/rest/api/space", limit=limit)
            return result.get("results", [])  # type: ignore[return-value]

        # Basic tokens can vary by tenant/scopes; prefer v2 and fall back to v1 on auth failures.
        try:
            result = self._get("/wiki/api/v2/spaces", limit=limit)
            return result.get("results", [])  # type: ignore[return-value]
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (401, 403):
                raise
            result = self._get("/wiki/rest/api/space", limit=limit)
            return result.get("results", [])  # type: ignore[return-value]

    def search_cql(self, cql: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """Run search cql."""
        result = self._get("/wiki/rest/api/content/search", cql=cql, limit=limit, start=0)
        return result.get("results", [])  # type: ignore[return-value]

    def get_current_user(self) -> dict[str, Any]:
        """Return the Atlassian account details for the authenticated service account."""
        result = self._get("/wiki/rest/api/user/current")
        return result  # type: ignore[return-value]

    def resolve_canonical_url(self, webui_path: str) -> str:
        """Build an absolute URL from a relative _links.webui value."""
        if webui_path.startswith("http"):
            return webui_path
        return f"{self._site_base_url}{webui_path}"

    @property
    def site_base_url(self) -> str:
        """Run site base url."""
        return self._site_base_url

    def _normalise_v1_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run normalise v1 page."""
        body = (payload.get("body") or {}).get("storage") or {}
        version = payload.get("version") or {}
        by = version.get("by") or {}
        when = version.get("when") or ""
        space = payload.get("space") or {}
        history = payload.get("history") or {}
        created_by = history.get("createdBy") or {}
        links = payload.get("_links") or {}
        return {
            "id": payload.get("id") or "",
            "title": payload.get("title") or "",
            "spaceId": space.get("key") or "",
            "body": {"storage": {"value": body.get("value") or ""}},
            "version": {
                "number": version.get("number"),
                "authorId": by.get("accountId") or "",
                "createdAt": when,
            },
            "ownerId": created_by.get("accountId") or "",
            "_links": {"webui": links.get("webui") or ""},
        }

    def _normalise_v1_comments(self, comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Run normalise v1 comments."""
        normalised: list[dict[str, Any]] = []
        for comment in comments:
            version = comment.get("version") or {}
            by = version.get("by") or {}
            normalised.append(
                {
                    "id": comment.get("id") or "",
                    "version": {"authorId": by.get("accountId") or ""},
                    "body": {
                        "storage": {
                            "value": ((comment.get("body") or {}).get("storage") or {}).get("value")
                            or ""
                        }
                    },
                }
            )
        return normalised


class _BearerTokenAuth(AuthBase):
    """BearerTokenAuth."""

    def __init__(self, token: str) -> None:
        """Run init."""
        self._token = token

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        """Run call."""
        request.headers["Authorization"] = f"Bearer {self._token}"
        return request


class _OAuthClientCredentialsAuth(AuthBase):
    """Fetches and refreshes OAuth access tokens using client credentials."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        audience: str | None = None,
    ) -> None:
        """Run init."""
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._audience = audience
        self._access_token: str = ""
        self._expires_at_epoch_s: float = 0.0

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        """Run call."""
        if self._needs_refresh():
            self._refresh_token()
        request.headers["Authorization"] = f"Bearer {self._access_token}"
        return request

    def _needs_refresh(self) -> bool:
        """Run needs refresh."""
        if not self._access_token:
            return True
        return time.time() >= self._expires_at_epoch_s

    def _refresh_token(self) -> None:
        """Run refresh token."""
        payload: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scope:
            payload["scope"] = self._scope
        if self._audience:
            payload["audience"] = self._audience

        resp = request_with_instrumentation(
            "POST",
            self._token_url,
            logger=logger,
            json=payload,
            timeout=20,
            system="confluence-auth",
            operation="oauth_token_refresh",
        )
        resp.raise_for_status()
        token_payload = resp.json()
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("OAuth token response did not include access_token")
        expires_in = int(token_payload.get("expires_in") or 3600)
        # Refresh a minute early to avoid expiry during requests.
        self._expires_at_epoch_s = time.time() + max(expires_in - 60, 30)
        self._access_token = access_token


# --------------------------------------------------------------------------- #
# MCP server                                                                   #
# --------------------------------------------------------------------------- #


class ConfluenceMCPServer:
    """ConfluenceMCPServer."""

    provider = "confluence"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        auth_email: str | None = None,
        api_token: str | None = None,
        oauth_access_token: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_token_url: str = "https://auth.atlassian.com/oauth/token",
        oauth_scope: str | None = None,
        oauth_audience: str | None = None,
        auth_mode: str = "basic",
        cloud_id: str | None = None,
        client: ConfluenceClient | None = None,
        account_id: str | None = None,
        mention_aliases: tuple[str, ...] | None = None,
    ) -> None:
        """Run init."""
        self._account_id = account_id or ""
        aliases = mention_aliases or ("@assessment-agent", "@compliance-agent")
        self._mention_aliases = tuple(a.strip() for a in aliases if a.strip())
        if client is not None:
            self._client: ConfluenceClient | None = client
        elif auth_mode == "basic" and base_url and auth_email and api_token:
            self._client = ConfluenceClient(
                base_url=base_url,
                auth_email=auth_email,
                api_token=api_token,
                auth_mode="basic",
            )
        elif auth_mode == "bearer" and base_url and api_token and cloud_id:
            self._client = ConfluenceClient(
                base_url=base_url,
                api_token=api_token,
                auth_mode="bearer",
                cloud_id=cloud_id,
            )
        elif (
            auth_mode == "oauth"
            and base_url
            and cloud_id
            and (oauth_access_token or (oauth_client_id and oauth_client_secret))
        ):
            self._client = ConfluenceClient(
                base_url=base_url,
                oauth_access_token=oauth_access_token,
                oauth_client_id=oauth_client_id,
                oauth_client_secret=oauth_client_secret,
                oauth_token_url=oauth_token_url,
                oauth_scope=oauth_scope,
                oauth_audience=oauth_audience,
                auth_mode="oauth",
                cloud_id=cloud_id,
            )
        else:
            self._client = None

    @property
    def client(self) -> ConfluenceClient | None:
        """Expose the underlying ConfluenceClient for direct API access."""
        return self._client

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def resolve_target(
        self, target_reference: str, *, requester_context: dict[str, Any] | None = None
    ) -> ResolvedTarget:
        """Run resolve target."""
        parsed = urlparse(target_reference.strip())
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("target_reference must be a valid absolute URL")
        host = (parsed.hostname or "").lower()
        is_site_url = _host_is_exact_or_subdomain(host, "atlassian.net")
        is_ex_api_url = host == "api.atlassian.com" and "/ex/confluence/" in parsed.path
        if not (is_site_url or is_ex_api_url):
            raise ValueError("target_reference does not look like a Confluence Cloud URL")

        page_id, space_key = _parse_confluence_url_path(parsed.path)
        title = parsed.path.rstrip("/").split("/")[-1].replace("+", " ")
        canonical_url = target_reference.strip()

        if self._client is not None and page_id:
            page = self._client.get_page(page_id)
            title = page.get("title") or title
            webui = (page.get("_links") or {}).get("webui") or ""
            if webui:
                canonical_url = self._client.resolve_canonical_url(webui)
            if not space_key:
                space_id = page.get("spaceId") or ""
                if space_id:
                    space = self._client.get_space(space_id)
                    space_key = space.get("key") or ""

        return ResolvedTarget(
            provider="confluence",
            target_type="page",
            target_id=page_id or _url_fallback_id(target_reference),
            canonical_url=canonical_url,
            title=title,
            container_id=space_key or "",
            metadata={"space_key": space_key or ""},
        )

    def check_user_access(
        self, target_id: str, delegated_user_context: dict[str, Any]
    ) -> AccessDecision:
        """Run check user access."""
        principal = str(delegated_user_context.get("principal_id") or "").strip()
        email = str(delegated_user_context.get("email") or "").strip()
        if not (principal or email):
            return AccessDecision(
                granted=False,
                identity_mode="delegated",
                reason="missing delegated principal context",
                audit_fields={"target_id": target_id},
            )

        granted: bool
        reason: str
        if self._client is not None:
            try:
                self._client.get_page(target_id)
                granted = True
                reason = "page readable under current credentials"
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (403, 404):
                    granted = False
                    reason = "page not accessible"
                else:
                    raise
        else:
            granted = True
            reason = "granted (offline mode)"

        return AccessDecision(
            granted=granted,
            identity_mode="delegated",
            reason=reason,
            audit_fields={"target_id": target_id, "principal_id": principal},
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

        if self._client is None:
            return self._offline_content_stub(target_id, identity_mode, include_discussion_context)

        page = self._client.get_page(target_id)
        title = page.get("title") or target_id
        webui = (page.get("_links") or {}).get("webui") or ""
        canonical_url = self._client.resolve_canonical_url(webui) if webui else target_id

        storage_html = (page.get("body") or {}).get("storage", {}).get("value") or ""
        content = _strip_html(storage_html) if storage_html else ""

        version_block = page.get("version") or {}
        last_editor = self._resolve_person(version_block.get("authorId"))
        owner = self._resolve_person(page.get("ownerId"))

        discussion: list[dict[str, Any]] = []
        if include_discussion_context:
            raw_comments = self._client.get_footer_comments(target_id)
            discussion = _normalise_comments(raw_comments)

        return AssessedArtifactPackage(
            provider="confluence",
            target_id=target_id,
            canonical_url=canonical_url,
            title=title,
            content=content,
            metadata={"identity_mode": identity_mode, "version": version_block.get("number")},
            owner=owner,
            last_editor=last_editor,
            discussion_context=discussion,
        )

    def get_recent_mentions(
        self,
        *,
        since: str = "",
        lookback_window: str = "",
        scope_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run get recent mentions."""
        if self._client is None:
            return {"mentions": []}

        since_dt = self._resolve_since(since=since, lookback_window=lookback_window)

        filters: list[str] = []
        if self._account_id:
            filters.append(f'mention = "{self._account_id}"')
        else:
            alias_filters = [f'text ~ "{alias}"' for alias in self._mention_aliases]
            if alias_filters:
                filters.append("(" + " OR ".join(alias_filters) + ")")
            else:
                filters.append('text ~ "@assessment-agent"')

        if since_dt:
            filters.append(f'created >= "{_iso_to_cql_datetime(since_dt)}"')

        if scope_filter:
            space_key = scope_filter.get("space_key")
            space_keys: list[str] = scope_filter.get("space_keys") or (
                [space_key] if space_key else []
            )
            if len(space_keys) == 1:
                filters.append(f'space.key = "{space_keys[0]}"')
            elif len(space_keys) > 1:
                keys_cql = ", ".join(f'"{k}"' for k in space_keys)
                filters.append(f"space.key IN ({keys_cql})")

        cql = " AND ".join(filters) + " ORDER BY created ASC"
        try:
            results = self._client.search_cql(cql)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body = (exc.response.text if exc.response is not None else "") or ""
            if status == 401 and "scope does not match" in body.lower():
                raise PermissionError(
                    "Confluence CQL search scope is missing for get_recent_mentions. "
                    "Grant search:confluence (classic) or read:content-details:confluence (granular)."
                ) from exc
            raise
        site_base = self._client.site_base_url
        mentions = [_normalise_mention_result(r, site_base_url=site_base) for r in results]
        return {"mentions": mentions}

    def _resolve_since(self, *, since: str, lookback_window: str) -> str:
        """Run resolve since."""
        if since:
            return since
        if lookback_window:
            return _iso_duration_to_since(lookback_window)
        return (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    def get_flagged_item_context(
        self,
        target_id: str,
        *,
        identity_mode: str,
        trigger_context: dict[str, Any] | None = None,
    ) -> AssessedArtifactPackage:
        """Run get flagged item context."""
        artifact = self.get_content_by_id(
            target_id,
            identity_mode=identity_mode,
            include_discussion_context=True,
        )
        merged_metadata = {**artifact.metadata, "trigger_context": dict(trigger_context or {})}
        return AssessedArtifactPackage(
            provider=artifact.provider,
            target_id=artifact.target_id,
            canonical_url=artifact.canonical_url,
            title=artifact.title,
            content=artifact.content,
            metadata=merged_metadata,
            owner=artifact.owner,
            last_editor=artifact.last_editor,
            discussion_context=artifact.discussion_context,
        )

    def resolve_page_owner(self, target_id: str) -> dict[str, Any]:
        """Run resolve page owner."""
        if self._client is None:
            return {"principal_id": f"owner-{target_id}", "display_name": "Stub Owner", "email": ""}
        page = self._client.get_page(target_id)
        owner_id = page.get("ownerId") or ""
        if not owner_id:
            return {"principal_id": "", "display_name": "Unknown", "email": ""}
        user = self._client.get_user(owner_id)
        return {
            "principal_id": user.get("accountId") or owner_id,
            "display_name": user.get("displayName") or "",
            "email": user.get("email") or "",
        }

    def resolve_last_editor(self, target_id: str) -> dict[str, Any]:
        """Run resolve last editor."""
        if self._client is None:
            return {
                "principal_id": f"editor-{target_id}",
                "display_name": "Stub Editor",
                "email": "",
                "modified_at": "2026-04-02T00:00:00Z",
            }
        page = self._client.get_page(target_id)
        version_block = page.get("version") or {}
        author_id = version_block.get("authorId") or ""
        modified_at = version_block.get("createdAt") or ""
        if not author_id:
            return {
                "principal_id": "",
                "display_name": "Unknown",
                "email": "",
                "modified_at": modified_at,
            }
        user = self._client.get_user(author_id)
        return {
            "principal_id": user.get("accountId") or author_id,
            "display_name": user.get("displayName") or "",
            "email": user.get("email") or "",
            "modified_at": modified_at,
        }

    def post_comment(
        self,
        target_id: str,
        *,
        comment_body: str,
        identity_mode: str,
        idempotency_key: str,
    ) -> DeliveryOutcome:
        """Run post comment."""
        if self._client is None:
            raise NotImplementedError(
                "Confluence comment publication requires a live ConfluenceClient"
            )
        # Sanitise key so it cannot break the embedded HTML comment.
        safe_key = re.sub(r"[^a-zA-Z0-9_\-]", "", idempotency_key)
        body_html = f"<!-- assessment-idempotency-key: {safe_key} -->\n{comment_body}"
        try:
            result = self._client.post_footer_comment(target_id, body_html=body_html)
            comment_id = result.get("id") or ""
            return DeliveryOutcome(
                success=True,
                attempted_channels=("inline",),
                metadata={"comment_id": comment_id, "idempotency_key": idempotency_key},
            )
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            return DeliveryOutcome(
                success=False,
                attempted_channels=("inline",),
                failures=(f"http_{status}",),
                metadata={"idempotency_key": idempotency_key},
            )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _resolve_person(self, account_id: str | None) -> PersonReference | None:
        """Run resolve person."""
        if not account_id or self._client is None:
            return None
        try:
            user = self._client.get_user(account_id)
            return PersonReference(
                principal_id=user.get("accountId") or account_id,
                display_name=user.get("displayName") or "",
                email=user.get("email") or "",
            )
        except requests.HTTPError:
            return None

    def _offline_content_stub(
        self,
        target_id: str,
        identity_mode: str,
        include_discussion_context: bool,
    ) -> AssessedArtifactPackage:
        """Run offline content stub."""
        content = (
            "This is stub Confluence content for orchestration wiring. "
            f"Target ID: {target_id}. Identity mode: {identity_mode}."
        )
        discussion: list[dict[str, Any]] = []
        if include_discussion_context:
            discussion = [{"author": "stub-user", "text": "Please assess this content."}]
        return AssessedArtifactPackage(
            provider="confluence",
            target_id=target_id,
            canonical_url=f"https://example.atlassian.net/wiki/pages/{target_id}",
            title=f"confluence-{target_id}",
            content=content,
            metadata={"source": "stub", "identity_mode": identity_mode},
            discussion_context=discussion,
        )


# --------------------------------------------------------------------------- #
# Module helpers                                                               #
# --------------------------------------------------------------------------- #


class MentionPoller:
    """Stateful CQL polling loop for Confluence @mention detection.

    Maintains a watermark timestamp so successive calls only return new mentions.
    The watermark is stored as an ISO 8601 string and can be persisted and restored
    by the caller for crash-safe operation.
    """

    def __init__(
        self,
        server: ConfluenceMCPServer,
        *,
        space_keys: list[str] | None = None,
        initial_lookback: str = "PT1H",
    ) -> None:
        """Run init."""
        self._server = server
        self._space_keys = space_keys
        self._initial_lookback = initial_lookback
        self._watermark: str = ""

    @property
    def watermark(self) -> str:
        """Current high-water mark as ISO 8601 string. Empty string on first poll."""
        return self._watermark

    @watermark.setter
    def watermark(self, value: str) -> None:
        """Run watermark."""
        self._watermark = value

    def poll(self) -> list[dict[str, Any]]:
        """Return new mentions since last watermark and advance the watermark."""
        scope: dict[str, Any] | None = (
            {"space_keys": self._space_keys} if self._space_keys else None
        )
        result = self._server.get_recent_mentions(
            since=self._watermark if self._watermark else "",
            lookback_window=self._initial_lookback if not self._watermark else "",
            scope_filter=scope,
        )
        mentions: list[dict[str, Any]] = result.get("mentions") or []

        # Advance watermark to the latest occurred_at in this batch
        for mention in mentions:
            occurred = str(mention.get("occurred_at") or "")
            if occurred and occurred > self._watermark:
                self._watermark = occurred

        return mentions


def _normalise_comments(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run normalise comments."""
    result = []
    for comment in raw:
        body_html = (comment.get("body") or {}).get("storage", {}).get("value") or ""
        text = _strip_html(body_html)
        version = comment.get("version") or {}
        result.append(
            {
                "comment_id": comment.get("id") or "",
                "author_id": version.get("authorId") or "",
                "text": text,
            }
        )
    return result
