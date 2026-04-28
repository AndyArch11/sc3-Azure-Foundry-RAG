"""Local provider initialisation — JSONL document loading for local/dev mode."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.search.abstract import SearchClient

logger = logging.getLogger(__name__)


def _infer_local_corpus(source_path: str, explicit_corpus: str) -> str:
    """Infer corpus label for local evidence docs.

    Priority:
    1) Explicit corpus value from payload when provided
    2) Source path hints for corpus-b / corpus-c
    3) Default to corpus-c for local evidence chunks
    """
    corpus = (explicit_corpus or "").strip().lower()
    if corpus in {"a", "b", "c"}:
        return corpus

    normalised_path = (source_path or "").replace("\\", "/").lower()
    if "/corpus-b/" in normalised_path:
        return "b"
    if "/corpus-c/" in normalised_path:
        return "c"

    return "c"


def _resolve_local_jsonl_paths() -> tuple[str, str]:
    """Resolve local evidence/controls JSONL paths with sensible local defaults.

    Preference order:
    1) Explicit env var value when provided
    2) Workspace-relative default for local/dev runs
    3) Container default path for compose/runtime images
    """
    evidence_path = (
        os.getenv("LOCAL_EVIDENCE_JSONL_PATH", "").strip() or "./runtime/out/chunks.jsonl"
    )

    controls_env = os.getenv("LOCAL_CONTROLS_JSONL_PATH", "").strip()
    if controls_env:
        return evidence_path, controls_env

    workspace_controls = Path("./parsed-controls")
    if workspace_controls.exists():
        return evidence_path, str(workspace_controls)

    return evidence_path, "/app/parsed-controls"


def _load_local_jsonl_documents(path_value: str, *, controls_mode: bool) -> list[dict[str, Any]]:
    """Load JSONL documents from local file(s) for in-memory or Qdrant indexing.

    Parameters
    ----------
    path_value : str
        Path to JSONL file or directory containing JSONL files.
    controls_mode : bool
        If True, parse as controls (requirement_id, framework, etc.).
        If False, parse as evidence chunks (content, source_name, corpus, etc.).

    Returns
    -------
    list[dict[str, Any]]
        Parsed and normalised documents ready for search client indexing.
    """
    path_text = (path_value or "").strip()
    if not path_text:
        return []

    path = Path(path_text)
    if not path.exists():
        logger.warning("Local JSONL path not found: %s", path)
        return []

    files: list[Path]
    if path.is_dir():
        files = sorted(path.glob("*.jsonl"))
    else:
        files = [path]

    docs: list[dict[str, Any]] = []
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(payload, dict):
                        continue

                    if controls_mode:
                        req_text = str(payload.get("requirement_text") or "").strip()
                        if not req_text:
                            continue
                        docs.append(
                            {
                                "requirement_id": payload.get("requirement_id") or "",
                                "framework": payload.get("framework") or "",
                                "framework_version": payload.get("framework_version") or "",
                                "control_family": payload.get("control_family") or "",
                                "maturity_level": payload.get("maturity_level"),
                                "requirement_text": req_text,
                                "guidance_text": payload.get("guidance_text") or "",
                                "source_uri": payload.get("source_uri") or str(file_path.name),
                                "@search.score": 1.0,
                            }
                        )
                    else:
                        content = str(payload.get("content") or "").strip()
                        if not content:
                            continue
                        source_path = str(payload.get("source_path") or "")
                        corpus = _infer_local_corpus(
                            source_path,
                            str(payload.get("corpus") or ""),
                        )
                        default_role = "narrative_guidance" if corpus == "b" else "evidence"
                        docs.append(
                            {
                                "id": payload.get("id")
                                or payload.get("chunk_id")
                                or f"{file_path.name}:{len(docs)}",
                                "content": content,
                                "source_name": (
                                    Path(source_path).name if source_path else file_path.name
                                ),
                                "source_path": source_path,
                                "corpus": corpus,
                                "corpus_role": payload.get("corpus_role") or default_role,
                                "upload_source": payload.get("upload_source") or "local",
                                "uploaded_by": payload.get("uploaded_by") or "local",
                                "upload_batch": payload.get("upload_batch") or "local",
                                "uploaded_at": payload.get("uploaded_at") or "",
                                "original_filename": payload.get("original_filename")
                                or Path(source_path).name,
                                "content_sha256": payload.get("content_sha256") or "",
                                "normalised_text_sha256": payload.get("normalised_text_sha256")
                                or "",
                                "dedupe_hash": payload.get("dedupe_hash") or "",
                                "dedupe_method": payload.get("dedupe_method") or "",
                                "@search.score": 1.0,
                            }
                        )
        except Exception as exc:
            logger.warning("Failed loading local JSONL %s: %s", file_path, exc)
    return docs


def load_local_documents_if_needed(
    search_client: SearchClient,
    controls_search_client: SearchClient,
) -> None:
    """Load local JSONL documents into search clients when running in local/dev mode.

    This function is a no-op when not running in local/dev mode and gracefully
    handles missing JSONL paths.

    Parameters
    ----------
    search_client : SearchClient
        Search client for evidence/documents. Must support load_documents() method.
    controls_search_client : SearchClient
        Search client for compliance controls. Must support load_documents() method.
    """
    provider = os.getenv("CLOUD_PROVIDER", "azure").strip().lower()
    if provider not in {"local", "dev"}:
        return

    local_evidence_path, local_controls_path = _resolve_local_jsonl_paths()

    try:
        # Both LocalInMemorySearchClient and LocalQdrantSearchClient define load_documents()
        if hasattr(search_client, "load_documents"):
            search_client.load_documents(
                _load_local_jsonl_documents(local_evidence_path, controls_mode=False)
            )
        if hasattr(controls_search_client, "load_documents"):
            controls_search_client.load_documents(
                _load_local_jsonl_documents(local_controls_path, controls_mode=True)
            )
        logger.warning(
            "Running in local mode with JSONL-backed indexes: evidence=%s controls=%s",
            local_evidence_path,
            local_controls_path,
        )
    except Exception as exc:
        logger.warning("Failed to initialise local indexes: %s", exc)
