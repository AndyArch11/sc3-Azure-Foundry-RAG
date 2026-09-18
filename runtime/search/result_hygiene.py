"""Shared search-result hygiene helpers.

These helpers provide optional overfetch + duplicate suppression across search
adapters. Cloud adapters keep this disabled by default and can opt in via env
flags. Local Qdrant keeps duplicate suppression enabled by default to preserve
current local-eval behaviour.
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    """Return a boolean value from an environment variable.

    Args:
        name: The name of the environment variable.
        default: The default boolean value if the environment variable is not set.

    Returns:
        The boolean value of the environment variable.
    """
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def dedupe_enabled(*, provider_key: str, default: bool) -> bool:
    """Return whether duplicate suppression is enabled for a given provider.

    Args:
        provider_key: The key identifying the search provider (e.g., "azure", "aws").
        default: The default boolean value if the environment variable is not set.

    Returns:
        True if duplicate suppression is enabled, False otherwise.
    """
    provider_env = f"{provider_key.upper()}_SEARCH_DEDUPE_ENABLED"
    if provider_env in os.environ:
        return _env_bool(provider_env, default)
    return _env_bool("SEARCH_RESULT_DEDUPE_ENABLED", default)


def overfetch_multiplier(*, provider_key: str, default: int = 4) -> int:
    """Return the overfetch multiplier for a given provider.

    Args:
        provider_key: The key identifying the search provider (e.g., "azure", "aws").
        default: The default multiplier value if the environment variable is not set.
    Returns:
        The overfetch multiplier as an integer.
    """
    provider_env = f"{provider_key.upper()}_SEARCH_OVERFETCH_MULTIPLIER"
    raw = str(
        os.getenv(provider_env, os.getenv("SEARCH_RESULT_OVERFETCH_MULTIPLIER", str(default)))
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(1, value)


def compute_search_limit(
    *,
    top: int,
    provider_key: str,
    default_dedupe_enabled: bool,
) -> int:
    """Compute the effective search limit based on overfetch and deduplication settings.

    Args:
        top: The maximum number of results requested.
        provider_key: The key identifying the search provider (e.g., "azure", "aws").
        default_dedupe_enabled: The default boolean value for deduplication if not set in env.

    Returns:
        The effective search limit after applying overfetch and deduplication logic.
    """
    limit = max(1, int(top))
    if limit <= 1:
        return limit
    if not dedupe_enabled(provider_key=provider_key, default=default_dedupe_enabled):
        return limit
    return max(limit, limit * overfetch_multiplier(provider_key=provider_key))


def dedupe_key(doc: dict[str, object]) -> str:
    """Return a stable duplicate-suppression key for retrieved payloads.

    Args:
        doc: The document for which to generate the deduplication key.

    Returns:
        A string representing the deduplication key.
    """
    for field in ("dedupe_hash", "content_sha256", "normalised_text_sha256"):
        value = str(doc.get(field) or "").strip()
        if value:
            return value

    source_name = str(doc.get("source_name") or "").strip().lower()
    content = str(doc.get("content") or "").strip().lower()
    return f"fallback:{source_name}:{content[:160]}"


def dedupe_results(
    items: list[dict[str, object]],
    *,
    top: int,
    provider_key: str,
    default_dedupe_enabled: bool,
) -> list[dict[str, object]]:
    """Preserve order while suppressing duplicate-like retrieval hits.
    
    Args:
        items: The list of retrieved items to deduplicate.
        top: The maximum number of items to return.
        provider_key: The key identifying the search provider (e.g., "azure", "aws").
        default_dedupe_enabled: The default boolean value for deduplication if not set in env.

    Returns:
        A list of deduplicated items, preserving the original order.
    """
    if not dedupe_enabled(provider_key=provider_key, default=default_dedupe_enabled):
        return items[:top]

    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for item in items:
        key = dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= top:
            break
    return deduped