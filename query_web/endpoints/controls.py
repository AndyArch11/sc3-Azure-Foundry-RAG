"""Framework-scoped controls retrieval endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from query_web.corpus_a import _CORPUS_A_FRAMEWORKS, _normalise_corpus_a_framework_key
from query_web.endpoints.problem_details import problem_response as _problem_response
from query_web.local_startup import _resolve_local_jsonl_paths

_FRAMEWORK_JSONL_BASENAMES: dict[str, tuple[str, str]] = {
    "aescsf": ("aescsf_v2-enriched.jsonl", "aescsf_v2.jsonl"),
    "cis_controls": ("cis_controls_v8-enriched.jsonl", "cis_controls_v8.jsonl"),
    "essential_eight": (
        "essential_eight_november-2023-enriched.jsonl",
        "essential_eight_november-2023.jsonl",
    ),
    "ism": ("ism_latest-enriched.jsonl", "ism_latest.jsonl"),
    "nist_ai_rmf": ("nist_ai_rmf_1-0-enriched.jsonl", "nist_ai_rmf_1-0.jsonl"),
    "nist_csf": ("nist_csf_2-0-enriched.jsonl", "nist_csf_2-0.jsonl"),
    "nist_sp_800_53": ("nist_sp_800_53_rev5-enriched.jsonl", "nist_sp_800_53_rev5.jsonl"),
    "pci_dss": ("pci_dss_v4_0_1-enriched.jsonl", "pci_dss_v4_0_1.jsonl"),
    "pspf": ("pspf_release_2025-enriched.jsonl", "pspf_release_2025.jsonl"),
}


def _controls_root_path() -> Path:
    """Return the root path for framework controls JSONL files.

    Returns:
        A Path object pointing to the directory containing framework controls JSONL files."""
    _, controls_path = _resolve_local_jsonl_paths()
    return Path(controls_path)


def _framework_controls_file(framework_key: str) -> Path | None:
    """Return the path to the controls JSONL file for a given framework key.

    Args:
        framework_key: The key representing the framework (e.g., "aescsf", "cis_controls").
    Returns:
        A Path object pointing to the controls JSONL file if it exists, or None if not found."""
    root = _controls_root_path()
    for basename in _FRAMEWORK_JSONL_BASENAMES.get(framework_key, ()):  # pragma: no branch
        candidate = root / basename
        if candidate.is_file():
            return candidate
    return None


def _load_framework_controls(framework_key: str) -> list[dict[str, Any]]:
    """Load and return the controls for a given framework key.

    Args:
        framework_key: The key representing the framework (e.g., "aescsf", "cis_controls").
    Returns:
        A list of dictionaries, each representing a control with its associated metadata.
    """
    file_path = _framework_controls_file(framework_key)
    if file_path is None:
        return []

    items: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                continue
            requirement_text = str(payload.get("requirement_text") or "").strip()
            if not requirement_text:
                continue
            items.append(
                {
                    "requirement_id": payload.get("requirement_id") or "",
                    "framework": payload.get("framework")
                    or _CORPUS_A_FRAMEWORKS.get(framework_key, ""),
                    "framework_version": payload.get("framework_version") or "",
                    "control_family": payload.get("control_family") or "",
                    "maturity_level": payload.get("maturity_level"),
                    "requirement_text": requirement_text,
                    "guidance_text": payload.get("guidance_text") or "",
                    "control_baselines": payload.get("control_baselines") or [],
                    "source_uri": payload.get("source_uri") or file_path.name,
                }
            )
    return items


def register_controls_endpoints(
    app: FastAPI,
    svc: Any | None = None,
    *,
    deps: dict[str, Any] | None = None,
) -> None:
    """Register framework-scoped controls retrieval endpoints.

    Args:
        app: The FastAPI application instance.
        svc: Optional service object providing dependencies (default is None).
        deps: Optional dictionary of dependencies to override service attributes (default is None).
    """

    if svc is None:
        svc = {}

    class _SvcAdapter:
        """Adapter to provide attribute access to the service object or dependencies dictionary.

        Attributes:
            svc: The service object providing dependencies.
            deps: Optional dictionary of dependencies to override service attributes.
        """

        def __getattr__(self, name: str) -> Any:
            if isinstance(deps, dict) and name in deps:
                candidate = deps[name]
                return candidate() if callable(candidate) else candidate
            if isinstance(svc, dict):
                if name in svc:
                    candidate = svc[name]
                    return candidate() if callable(candidate) else candidate
                raise AttributeError(name)
            return getattr(svc, name)

    svc = _SvcAdapter()

    @app.get("/api/frameworks/{framework}/controls")
    def framework_controls(
        request: Request,
        framework: str,
        auth_token: str = Query(default=""),
    ) -> JSONResponse:
        """Retrieve controls for a specified framework.

        Args:
            request: The FastAPI request object.
            framework: The key representing the framework (e.g., "aescsf", "cis_controls").
            auth_token: The authentication token for the request.

        Returns:
            A JSONResponse containing the framework controls or an error message if the request is unauthorised or the framework is unknown.
        """
        if not bool(svc._is_authorised_request(auth_token, request)):
            return _problem_response(
                status=401,
                title="Unauthorised",
                detail=str(svc._unauthorised_message(request)),
                instance=str(request.url.path),
            )

        framework_key = _normalise_corpus_a_framework_key(framework)
        if framework_key is None or framework_key == "all":
            return _problem_response(
                status=404,
                title="Not Found",
                detail=f"Unknown framework: {framework}",
                instance=str(request.url.path),
            )

        items = _load_framework_controls(framework_key)
        if not items:
            return _problem_response(
                status=404,
                title="Not Found",
                detail=f"No controls found for framework: {framework}",
                instance=str(request.url.path),
                extensions={"framework_key": framework_key},
            )

        return JSONResponse(
            {
                "framework_key": framework_key,
                "framework": _CORPUS_A_FRAMEWORKS[framework_key],
                "total_count": len(items),
                "items": items,
            }
        )
