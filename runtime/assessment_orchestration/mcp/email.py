"""
Email MCP Server Module.

This module defines the EmailMCPServer class, which provides an implementation for handling email-based notifications and interactions within the MCP (Message Control Protocol) framework.
It includes methods for reading inbox notifications, parsing notification targets, resolving recipients, sending emails, and marking notifications as processed.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ..models import DeliveryOutcome


class EmailMCPServer:
    """EmailMCPServer.

    An MCP server implementation for handling email-based notifications and interactions. This class provides methods to read inbox notifications, parse notification targets, resolve recipients, send emails, and mark notifications as processed. It is designed to be extended with actual email service implementations.

    Attributes:
        _URL_PATTERN: A compiled regular expression pattern to match URLs in email content.
    """

    provider = "email"

    _URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)

    @staticmethod
    def _host_is_exact_or_subdomain(host: str, domain: str) -> bool:
        """Check if the host is exactly the domain or a subdomain of it.

        Args:
            host: The host to check.
            domain: The domain to compare against.

        Returns:
            True if the host is exactly the domain or a subdomain of it, False otherwise.
        """
        host_l = host.strip().lower()
        domain_l = domain.strip().lower()
        return host_l == domain_l or host_l.endswith(f".{domain_l}")

    def read_inbox_notifications(
        self,
        mailbox_id: str,
        *,
        lookback_window: str,
        folder: str = "Inbox",
        unread_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Run read inbox notifications.

        Args:
            mailbox_id: The ID of the mailbox to read notifications from.
            lookback_window: The time window to look back for notifications (e.g., "PT1H" for 1 hour).
            folder: The folder to read notifications from (default is "Inbox").
            unread_only: Whether to only read unread notifications (default is True).

        Returns:
            A list of notification dictionaries containing details of the notifications read from the inbox.

        Raises:
            NotImplementedError: This method is not implemented yet and should be overridden in a subclass.
        """
        raise NotImplementedError("Email inbox notification retrieval is not implemented yet")

    def parse_notification_target(self, message: dict[str, Any]) -> dict[str, Any]:
        """Run parse notification target.

        Args:
            message: A dictionary representing the email notification message to parse.

        Returns:
            A dictionary containing the parsed notification target information.
        """
        body = str(message.get("body") or "")
        subject = str(message.get("subject") or "")
        text = f"{subject}\n{body}"

        match = self._URL_PATTERN.search(text)
        target_reference = match.group(0) if match else ""
        provider = "unknown"
        if target_reference:
            parsed = urlparse(target_reference)
            host = (parsed.hostname or "").lower()
            if self._host_is_exact_or_subdomain(host, "atlassian.net") or (
                host == "api.atlassian.com" and "/ex/confluence/" in (parsed.path or "")
            ):
                provider = "confluence"
            elif self._host_is_exact_or_subdomain(
                host, "sharepoint.com"
            ) or self._host_is_exact_or_subdomain(host, "office.com"):
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
        """Run resolve recipients.

        Args:
            recipient_candidates: A list of candidate email addresses to resolve.
            policy: An optional policy string to determine how recipients are resolved.

        Returns:
            A list of resolved email addresses.
        """
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
        """Run send email.

        Args:
            recipients: A list of email addresses to send the email to.
            subject: The subject of the email.
            body: The body content of the email.
            idempotency_key: A unique key to ensure idempotent email delivery.

        Returns:
            A DeliveryOutcome object representing the result of the email delivery.
        Raises:
            NotImplementedError: This method is not implemented yet and should be overridden in a subclass.
        """
        raise NotImplementedError("Email delivery is not implemented yet")

    def mark_notification_processed(
        self, message_id: str, *, processing_state: str
    ) -> dict[str, Any]:
        """Run mark notification processed.

        Args:
            message_id: The ID of the message to mark as processed.
            processing_state: The state to mark the message with.

        Returns:
            A dictionary containing the result of the operation.
        """
        return {
            "success": True,
            "message_id": str(message_id).strip(),
            "processing_state": str(processing_state).strip(),
        }
