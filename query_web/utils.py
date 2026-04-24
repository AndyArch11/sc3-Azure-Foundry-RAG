"""Shared utilities and helpers used across multiple endpoint modules.

This module contains utility functions that don't depend on application state
and can be safely imported across multiple endpoint modules without creating
circular dependencies.

Auth, request introspection, and diagnostics functions are kept in app.py
to maintain a single source of truth for authorisation logic.
"""

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from query_web.constants import ALLOWED_EXTENSIONS, MIME_TYPE_BY_EXTENSION

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Get current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def _sanitise_blob_name_component(value: str) -> str:
    """Sanitise a component of a blob name to be Azure Storage compatible."""
    text = value.strip().replace("\\", "_").replace("/", "_")
    text = re.sub(r"[^A-Za-z0-9._-]", "_", text)
    return text[:120] or "file"


def _compute_normalised_text_hash(
    content: bytes,
    *,
    filename: str,
    content_type: str,
) -> tuple[str | None, str]:
    """Compute hash of normalised text content for deduplication.

    Returns (hash_hex, hash_method) or (None, "binary") for non-text.
    """
    text_exts = {
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".csv",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".log",
    }
    ext = Path(filename).suffix.lower()
    ctype = (content_type or "").lower()
    is_text_like = (
        ext in text_exts
        or ctype.startswith("text/")
        or "json" in ctype
        or "xml" in ctype
        or "yaml" in ctype
    )
    if not is_text_like:
        return None, "binary"

    decoded = content.decode("utf-8", errors="ignore")
    normalised = re.sub(r"\s+", " ", decoded).strip().lower()
    if not normalised:
        return None, "empty"
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest(), "normalised_text"


def _extract_dedupe_hashes(skipped: list[str]) -> list[str]:
    """Extract deduplication hashes from a list of skip messages."""
    hashes: list[str] = []
    pattern = re.compile(r"duplicate-[^:]+:([0-9a-f]{64})$", re.IGNORECASE)
    for item in skipped:
        match = pattern.search(str(item))
        if match:
            hashes.append(match.group(1))
    return list(dict.fromkeys(hashes))


def _dedupe_blob_prefix(corpus: str, dedupe_hash: str) -> str:
    """Generate blob prefix for deduplicated corpus artifacts."""
    return f"corpus-{corpus}/by-dedupe/{dedupe_hash}"


def sanitise_untrusted_text(text: str) -> str:
    """Sanitise untrusted text to prevent injection attacks.

    Delegates to the prompt_injection_guard module.
    """
    from query_web.security.prompt_injection_guard import sanitise_untrusted_text as guard_sanitise

    return guard_sanitise(text)


# ---------------------------------------------------------------------------
# Upload validation helpers
# ---------------------------------------------------------------------------


def _is_allowed_filetype(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


def _extension_matches_mime(filename: str, mime_type: str) -> bool:
    ext = Path(filename).suffix.lower()
    expected_mime = MIME_TYPE_BY_EXTENSION.get(ext)
    if not expected_mime:
        return False
    # Some browsers may send additional parameters (e.g., charset) in content_type
    return mime_type.split(";")[0].strip() == expected_mime


def _risk_label(value: str) -> str:
    normalised = str(value or "unknown").strip().replace("_", " ").lower()
    if normalised == "low":
        return "Low"
    if normalised == "medium":
        return "Medium"
    if normalised == "high":
        return "High"
    if normalised == "critical":
        return "Critical"
    return "Unknown"


def sanitise_conversation_turn(role: str, content: str) -> str:
    """Sanitise conversation history entries.

    Delegates to the prompt_injection_guard module.
    """
    from query_web.security.prompt_injection_guard import (
        sanitise_conversation_turn as guard_sanitise,
    )

    return guard_sanitise(role, content)
