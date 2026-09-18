"""Shared parser helper utilities.

Utilities in this module are intentionally small, deterministic helpers that are
reused across multiple framework parsers.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from logging import Logger
from typing import Any, Callable


def slugify_text(text: str, *, delimiter: str = "-") -> str:
    """Convert arbitrary text to a lowercase slug.

    Args:
        text: Input text to slugify.
        delimiter: Delimiter to use between token groups.

    Returns:
        Slugified text.
    """
    if delimiter not in {"-", "_"}:
        raise ValueError("delimiter must be '-' or '_'")
    replacement = "-" if delimiter == "-" else "_"
    slug = re.sub(r"[^a-z0-9]+", replacement, (text or "").lower()).strip(replacement)
    return slug


def normalise_space(text: str) -> str:
    """Collapse repeated whitespace and trim ends.

    Args:
        text: Input text value.

    Returns:
        Normalized text with single spaces.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def oscal_local_name(tag: str) -> str:
    """Return XML local-name without namespace prefix.

    Args:
        tag: XML tag name, potentially namespaced.

    Returns:
        Local name without namespace URI.
    """
    if "}" in tag:
        return tag.split("}", maxsplit=1)[1]
    return tag


def oscal_extract_part_text(part: ET.Element) -> str:
    """Extract normalised text from an OSCAL part element.

    Prefers explicit <prose> nodes when present and falls back to the whole
    part text otherwise.

    Args:
        part: OSCAL part element.

    Returns:
        Extracted text or empty string, with whitespace normalised.
    """
    prose_chunks: list[str] = []
    for node in part.iter():
        if oscal_local_name(node.tag) != "prose":
            continue
        value = normalise_space("".join(node.itertext()))
        if value:
            prose_chunks.append(value)

    if prose_chunks:
        return normalise_space(" ".join(prose_chunks))

    return normalise_space("".join(part.itertext()))


def oscal_first_direct_part_text(
    control: ET.Element,
    part_name: str,
    *,
    namespace: dict[str, str],
) -> str:
    """Get text from the first direct OSCAL part with the given name.

    Args:
        control: OSCAL control or group element.
        part_name: Part name to match, case-insensitive.
        namespace: ElementTree namespace map.

    Returns:
        First matching part text or empty string.
    """
    for part in control.findall("./oscal:part", namespace):
        if normalise_space(str(part.attrib.get("name") or "")).lower() != part_name.lower():
            continue
        text = oscal_extract_part_text(part)
        if text:
            return text
    return ""


def oscal_all_direct_part_text(
    control: ET.Element,
    part_name: str,
    *,
    namespace: dict[str, str],
) -> list[str]:
    """Get text from all direct OSCAL parts with the given name.

    Args:
        control: OSCAL control or group element.
        part_name: Part name to match, case-insensitive.
        namespace: ElementTree namespace map.

    Returns:
        List of matching part text values.
    """
    texts: list[str] = []
    for part in control.findall("./oscal:part", namespace):
        if normalise_space(str(part.attrib.get("name") or "")).lower() != part_name.lower():
            continue
        text = oscal_extract_part_text(part)
        if text:
            texts.append(text)
    return texts


def fetch_bytes_with_instrumentation(
    *,
    url: str,
    logger: Logger,
    timeout: int,
    system: str,
    operation: str,
    instrumenter: Callable[..., Any],
    request_callable: Callable[..., Any],
    headers: dict[str, str] | None = None,
) -> bytes:
    """Execute instrumented GET request and return raw response bytes.

    Args:
        url: Target URL.
        logger: Module logger used by instrumentation.
        timeout: Request timeout in seconds.
        system: Source system identifier for instrumentation tags.
        operation: Operation identifier for instrumentation tags.
        instrumenter: request_with_instrumentation-like callable.
        request_callable: Concrete request callable (typically requests.get).
        headers: Optional request headers.

    Returns:
        Response content as bytes.

    Raises:
        Exception: Any exception from the instrumenter or HTTP status failure.
    """
    response = instrumenter(
        "GET",
        url,
        logger=logger,
        timeout=timeout,
        headers=headers,
        system=system,
        operation=operation,
        request_callable=request_callable,
    )
    response.raise_for_status()
    return bytes(response.content)


def fetch_text_with_instrumentation(
    *,
    url: str,
    logger: Logger,
    timeout: int,
    system: str,
    operation: str,
    instrumenter: Callable[..., Any],
    request_callable: Callable[..., Any],
    headers: dict[str, str] | None = None,
) -> str:
    """Execute instrumented GET request and return decoded response text.

    Args:
        url: Target URL.
        logger: Module logger used by instrumentation.
        timeout: Request timeout in seconds.
        system: Source system identifier for instrumentation tags.
        operation: Operation identifier for instrumentation tags.
        instrumenter: request_with_instrumentation-like callable.
        request_callable: Concrete request callable (typically requests.get).
        headers: Optional request headers.

    Returns:
        Response content as string.

    Raises:
        Exception: Any exception from the instrumenter or HTTP status failure.
    """
    response = instrumenter(
        "GET",
        url,
        logger=logger,
        timeout=timeout,
        headers=headers,
        system=system,
        operation=operation,
        request_callable=request_callable,
    )
    response.raise_for_status()
    return str(response.text)


def fetch_json_dict_with_instrumentation(
    *,
    url: str,
    logger: Logger,
    timeout: int,
    system: str,
    operation: str,
    instrumenter: Callable[..., Any],
    request_callable: Callable[..., Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute instrumented GET request and return JSON payload as a dictionary.

    Supports both response.json() and response.content fallback decoding.

    Args:
        url: Target URL.
        logger: Module logger used by instrumentation.
        timeout: Request timeout in seconds.
        system: Source system identifier for instrumentation tags.
        operation: Operation identifier for instrumentation tags.
        instrumenter: request_with_instrumentation-like callable.
        request_callable: Concrete request callable (typically requests.get).
        headers: Optional request headers.

    Returns:
        Response JSON payload as a dictionary.

    Raises:
        Exception: Any exception from the instrumenter or HTTP status failure.
    """
    response = instrumenter(
        "GET",
        url,
        logger=logger,
        timeout=timeout,
        headers=headers,
        system=system,
        operation=operation,
        request_callable=request_callable,
    )
    response.raise_for_status()

    if hasattr(response, "json"):
        payload = response.json()
        if isinstance(payload, dict):
            return payload

    raw = bytes(response.content)
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Expected JSON object payload")
    return decoded


def parse_xml_root(xml_bytes: bytes) -> ET.Element:
    """Parse XML bytes and return the root element.

    Args:
        xml_bytes: Raw XML bytes to parse.

    Returns:
        The root ElementTree element of the parsed XML.
    """
    return ET.fromstring(xml_bytes)
