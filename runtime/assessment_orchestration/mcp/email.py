from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ..models import DeliveryOutcome


class EmailMCPServer:
    provider = "email"

    _URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)

    def read_inbox_notifications(
        self,
        mailbox_id: str,
        *,
        lookback_window: str,
        folder: str = "Inbox",
        unread_only: bool = True,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Email inbox notification retrieval is not implemented yet")

    def parse_notification_target(self, message: dict[str, Any]) -> dict[str, Any]:
        body = str(message.get("body") or "")
        subject = str(message.get("subject") or "")
        text = f"{subject}\n{body}"

        match = self._URL_PATTERN.search(text)
        target_reference = match.group(0) if match else ""
        provider = "unknown"
        if target_reference:
            host = (urlparse(target_reference).hostname or "").lower()
            if "atlassian.net" in host or "confluence" in host:
                provider = "confluence"
            elif "sharepoint.com" in host or "office.com" in host:
                provider = "sharepoint"

        return {
            "provider": provider,
            "target_reference": target_reference,
            "message_id": str(message.get("message_id") or "").strip(),
            "requester_email": str(message.get("requester_email") or "").strip(),
            "mentioner_email": str(message.get("mentioner_email") or "").strip(),
            "mailbox_id": str(message.get("mailbox_id") or "").strip(),
        }

    def resolve_recipients(self, recipient_candidates: list[str], *, policy: str = "") -> list[str]:
        seen: set[str] = set()
        resolved: list[str] = []
        for candidate in recipient_candidates:
            email = str(candidate).strip().lower()
            if not email or "@" not in email:
                continue
            if email in seen:
                continue
            seen.add(email)
            resolved.append(email)

        if policy.strip().lower() == "single_primary" and resolved:
            return [resolved[0]]
        return resolved

    def send_email(
        self,
        recipients: list[str],
        *,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> DeliveryOutcome:
        raise NotImplementedError("Email delivery is not implemented yet")

    def mark_notification_processed(self, message_id: str, *, processing_state: str) -> dict[str, Any]:
        return {
            "success": True,
            "message_id": str(message_id).strip(),
            "processing_state": str(processing_state).strip(),
        }
