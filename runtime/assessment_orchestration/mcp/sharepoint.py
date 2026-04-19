from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests  # type: ignore[import-untyped]
from requests.auth import AuthBase  # type: ignore[import-untyped]

from ..models import (
    AccessDecision,
    AssessedArtifactPackage,
    DeliveryOutcome,
    PersonReference,
    ResolvedTarget,
)

# --------------------------------------------------------------------------- #
# Custom bearer token auth                                                     #
# --------------------------------------------------------------------------- #


class BearerTokenAuth(AuthBase):
    """Http Bearer token authentication."""

    def __init__(self, token: str) -> None:
        """Run init."""
        self.token = token

    def __call__(self, r):
        """Run call."""
        r.headers["Authorization"] = f"Bearer {self.token}"
        return r


# --------------------------------------------------------------------------- #
# URL helpers                                                                  #
# --------------------------------------------------------------------------- #


def _parse_sharepoint_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Parse SharePoint URL and extract tenant, site, and item IDs.

    Handles:
      https://tenant.sharepoint.com/sites/{site}/SitePages/{page}
      https://tenant.sharepoint.com/sites/{site}/Shared%20Documents/{file}
      https://tenant-my.sharepoint.com/personal/{user}/Documents/{file}
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path
    qs = parse_qs(parsed.query)

    # Extract tenant from hostname
    if ".sharepoint.com" not in host:
        return None, None, None

    tenant = host.split(".")[0]

    # Try to extract item ID from query params
    item_id = (qs.get("id") or [""])[0]

    # Try to extract site and page from path
    path_parts = [p for p in path.split("/") if p]
    site_id: str | None = None
    item_id_from_path: str | None = None

    try:
        # Pattern: /sites/{site}/SitePages/{page}
        if "sites" in path_parts:
            idx = path_parts.index("sites")
            if idx + 1 < len(path_parts):
                site_id = path_parts[idx + 1]
        # Last path segment could be page/file ID
        if path_parts and not path_parts[-1].endswith(".aspx"):
            # For simple cases, use hash of URL as item ID
            item_id_from_path = None
    except (ValueError, IndexError):
        pass

    # Return item_id from query params if available, otherwise from path
    final_item_id = item_id or item_id_from_path
    return tenant, site_id, final_item_id


def _url_fallback_id(url: str) -> str:
    """Run url fallback id."""
    return hashlib.sha256(url.encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# SharePoint REST API client                                                   #
# --------------------------------------------------------------------------- #


class SharePointClient:
    """Thin wrapper around Microsoft Graph API for SharePoint access."""

    def __init__(
        self,
        *,
        tenant: str,
        site_id: str,
        graph_token: str,
        base_url: str = "https://graph.microsoft.com/v1.0",
    ) -> None:
        """Run init."""
        self._tenant = tenant
        self._site_id = site_id
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.auth = BearerTokenAuth(graph_token)
        self._session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, **params: Any) -> Any:
        """Run get."""
        url = f"{self._base_url}{path}"
        resp = self._session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        """Run post."""
        url = f"{self._base_url}{path}"
        resp = self._session.post(url, json=body)
        resp.raise_for_status()
        return resp.json()

    def get_site(self) -> dict[str, Any]:
        """Run get site."""
        result = self._get(f"/sites/{self._site_id}")
        return result  # type: ignore[return-value]

    def get_item(self, item_id: str) -> dict[str, Any]:
        """Run get item."""
        result = self._get(f"/sites/{self._site_id}/drive/items/{item_id}")
        return result  # type: ignore[return-value]

    def get_item_content(self, item_id: str) -> str:
        """Retrieve item content as text."""
        url = f"{self._base_url}/sites/{self._site_id}/drive/items/{item_id}/content"
        resp = self._session.get(url)
        resp.raise_for_status()
        # Try to decode as text; if it's HTML, strip tags
        content = resp.text
        if content.startswith("<"):
            content = _strip_html(content)
        return content

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Run get user."""
        result = self._get(f"/users/{user_id}")
        return result  # type: ignore[return-value]

    def get_drive_items_by_parent(self, parent_id: str) -> list[dict[str, Any]]:
        """Run get drive items by parent."""
        result = self._get(f"/sites/{self._site_id}/drive/items/{parent_id}/children")
        return result.get("value", [])  # type: ignore[return-value]

    def post_comment(self, item_id: str, *, body: str) -> dict[str, Any]:
        """Post a comment on an item."""
        result = self._post(
            f"/sites/{self._site_id}/drive/items/{item_id}/comments",
            {"text": body},
        )
        return result  # type: ignore[return-value]

    def search_items(self, query: str) -> list[dict[str, Any]]:
        """Search for items in the site."""
        result = self._post(
            f"/sites/{self._site_id}/drive/search(q='{query}')",
            {},
        )
        return result.get("value", [])  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# HTML stripping (reused from Confluence)                                     #
# --------------------------------------------------------------------------- #


def _strip_html(html: str) -> str:
    """Strip HTML tags from content."""
    from html.parser import HTMLParser

    class HTMLTextExtractor(HTMLParser):
        """HTMLTextExtractor."""

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

    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


# --------------------------------------------------------------------------- #
# MCP server                                                                   #
# --------------------------------------------------------------------------- #


class SharePointMCPServer:
    """SharePointMCPServer."""

    provider = "sharepoint"

    def __init__(
        self,
        *,
        tenant: str | None = None,
        site_id: str | None = None,
        graph_token: str | None = None,
        client: SharePointClient | None = None,
    ) -> None:
        """Run init."""
        if client is not None:
            self._client: SharePointClient | None = client
        elif tenant and site_id and graph_token:
            self._client = SharePointClient(tenant=tenant, site_id=site_id, graph_token=graph_token)
        else:
            self._client = None

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
        if "sharepoint.com" not in (parsed.hostname or ""):
            raise ValueError("target_reference does not look like a SharePoint URL")

        tenant, site_id, item_id = _parse_sharepoint_url(target_reference)
        if not tenant:
            raise ValueError("Could not parse SharePoint URL")

        title = parsed.path.rstrip("/").split("/")[-1] or "sharepoint-item"
        target_type = "page" if "sitepages" in parsed.path.lower() else "drive_item"
        canonical_url = target_reference.strip()
        container = site_id or tenant

        if self._client is not None and item_id:
            try:
                item = self._client.get_item(item_id)
                title = item.get("name") or title
                weburl = item.get("webUrl") or ""
                if weburl:
                    canonical_url = weburl
                if not site_id:
                    site_id = self._client._site_id
            except requests.HTTPError:
                pass

        return ResolvedTarget(
            provider="sharepoint",
            target_type=target_type,
            target_id=item_id or _url_fallback_id(target_reference),
            canonical_url=canonical_url,
            title=title,
            container_id=container,
            metadata={"site_id": site_id or "", "tenant": tenant},
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
                self._client.get_item(target_id)
                granted = True
                reason = "item readable under current credentials"
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (403, 404):
                    granted = False
                    reason = "item not accessible"
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

        item = self._client.get_item(target_id)
        title = item.get("name") or target_id
        weburl = item.get("webUrl") or ""
        canonical_url = weburl if weburl else target_id

        content = ""
        try:
            content = self._client.get_item_content(target_id)
        except Exception:
            content = f"[Content unavailable for item {target_id}]"

        last_modified = item.get("lastModifiedDateTime") or ""
        last_editor_id = (item.get("lastModifiedBy") or {}).get("user", {}).get("id") or ""
        owner_id = (item.get("createdBy") or {}).get("user", {}).get("id") or ""

        last_editor = self._resolve_person(last_editor_id) if last_editor_id else None
        owner = self._resolve_person(owner_id) if owner_id else None

        discussion: list[dict[str, Any]] = []
        if include_discussion_context:
            try:
                comments_result = self._client._get(
                    f"/sites/{self._client._site_id}/drive/items/{target_id}/comments"
                )
                raw_comments = comments_result.get("value", [])
                discussion = _normalise_comments(raw_comments)
            except Exception:
                pass

        return AssessedArtifactPackage(
            provider="sharepoint",
            target_id=target_id,
            canonical_url=canonical_url,
            title=title,
            content=content,
            metadata={"identity_mode": identity_mode, "modified_at": last_modified},
            owner=owner,
            last_editor=last_editor,
            discussion_context=discussion,
        )

    def get_recent_mentions(
        self, *, lookback_window: str, scope_filter: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run get recent mentions."""
        if self._client is None:
            return {"mentions": []}
        # SharePoint doesn't have direct mention indexing; would need to search comments
        # For now, return empty list
        return {"mentions": []}

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
        item = self._client.get_item(target_id)
        owner_id = (item.get("createdBy") or {}).get("user", {}).get("id") or ""
        if not owner_id:
            return {"principal_id": "", "display_name": "Unknown", "email": ""}
        user = self._client.get_user(owner_id)
        return {
            "principal_id": user.get("id") or owner_id,
            "display_name": user.get("displayName") or "",
            "email": user.get("mail") or user.get("userPrincipalName") or "",
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
        item = self._client.get_item(target_id)
        editor_id = (item.get("lastModifiedBy") or {}).get("user", {}).get("id") or ""
        modified_at = item.get("lastModifiedDateTime") or ""
        if not editor_id:
            return {
                "principal_id": "",
                "display_name": "Unknown",
                "email": "",
                "modified_at": modified_at,
            }
        user = self._client.get_user(editor_id)
        return {
            "principal_id": user.get("id") or editor_id,
            "display_name": user.get("displayName") or "",
            "email": user.get("mail") or user.get("userPrincipalName") or "",
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
                "SharePoint comment publication requires a live SharePointClient"
            )
        try:
            # Embed idempotency key in comment for deduplication
            safe_key = re.sub(r"[^a-zA-Z0-9_\-]", "", idempotency_key)
            body_with_key = f"<!-- assessment-idempotency-key: {safe_key} -->\n{comment_body}"
            result = self._client.post_comment(target_id, body=body_with_key)
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

    def _resolve_person(self, user_id: str | None) -> PersonReference | None:
        """Run resolve person."""
        if not user_id or self._client is None:
            return None
        try:
            user = self._client.get_user(user_id)
            return PersonReference(
                principal_id=user.get("id") or user_id,
                display_name=user.get("displayName") or "",
                email=user.get("mail") or user.get("userPrincipalName") or "",
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
            "This is stub SharePoint content for orchestration wiring. "
            f"Target ID: {target_id}. Identity mode: {identity_mode}."
        )
        discussion: list[dict[str, Any]] = []
        if include_discussion_context:
            discussion = [{"author": "stub-user", "text": "Please assess this content."}]
        return AssessedArtifactPackage(
            provider="sharepoint",
            target_id=target_id,
            canonical_url=f"https://example.sharepoint.com/items/{target_id}",
            title=f"sharepoint-{target_id}",
            content=content,
            metadata={"source": "stub", "identity_mode": identity_mode},
            discussion_context=discussion,
        )


# --------------------------------------------------------------------------- #
# Module helpers                                                               #
# --------------------------------------------------------------------------- #


def _normalise_comments(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run normalise comments."""
    result = []
    for comment in raw:
        text = comment.get("body", {}).get("content") or ""
        author_id = (comment.get("from") or {}).get("user", {}).get("id") or ""
        result.append(
            {
                "comment_id": comment.get("id") or "",
                "author_id": author_id,
                "text": text,
            }
        )
    return result
