"""Shared RFC 7807 Problem Details response helper."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def problem_response(
    *,
    status: int,
    title: str,
    detail: str,
    instance: str,
    type_uri: str = "about:blank",
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build an RFC 7807 / RFC 9457 Problem Details response.

    Args:
        status: The HTTP status code for the response.
        title: A short, human-readable summary of the problem type.
        detail: A human-readable explanation specific to this occurrence of the problem.
        instance: A URI reference that identifies the specific occurrence of the problem.
        type_uri: A URI reference that identifies the problem type (default is "about:blank").
        extensions: Optional dictionary of additional fields to include in the response.

    Returns:
        A JSONResponse object containing the problem details.
    """
    payload: dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": int(status),
        "detail": detail,
        "instance": instance,
    }
    if extensions:
        payload.update(extensions)
    return JSONResponse(content=payload, status_code=int(status))
