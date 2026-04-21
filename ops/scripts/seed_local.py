#!/usr/bin/env python3
"""Local index seeder for CLOUD_PROVIDER=local mode.

Seeds evidence and controls JSONL documents into Qdrant using Ollama embeddings.
Runs as a one-shot service before query-web starts in the local Docker Compose stack.

Usage
-----
    python ops/scripts/seed_local.py               # seed both indexes
    python ops/scripts/seed_local.py --check        # report counts, exit non-zero if empty
    python ops/scripts/seed_local.py --force        # re-seed even if collections are already populated
    python ops/scripts/seed_local.py --evidence-only
    python ops/scripts/seed_local.py --controls-only

Environment Variables
---------------------
    LOCAL_EVIDENCE_JSONL_PATH   Path to evidence chunks JSONL file or directory (default: ./runtime/out/chunks.jsonl)
    LOCAL_CONTROLS_JSONL_PATH   Path to controls JSONL directory (default: /app/parsed-controls)
    QDRANT_URL                  Qdrant HTTP base URL (default: http://localhost:6333)
    OLLAMA_BASE_URL             Ollama HTTP base URL (default: http://localhost:11434)
    OLLAMA_EMBEDDING_MODEL      Ollama model for embeddings (default: nomic-embed-text)
    EVIDENCE_INDEX              Qdrant collection name for evidence (default: grounding-index)
    CONTROLS_INDEX              Qdrant collection name for controls (default: controls-index)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seed_local")


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

def _env(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


QDRANT_URL = _env("QDRANT_URL", "http://localhost:6333")
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = _env("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
EVIDENCE_INDEX = _env("EVIDENCE_INDEX", "grounding-index")
CONTROLS_INDEX = _env("CONTROLS_INDEX", "controls-index")
EVIDENCE_PATH = _env("LOCAL_EVIDENCE_JSONL_PATH", "./runtime/out/chunks.jsonl")
CONTROLS_PATH = _env("LOCAL_CONTROLS_JSONL_PATH", "/app/parsed-controls")


# ---------------------------------------------------------------------------
# Readiness checks
# ---------------------------------------------------------------------------

def _wait_for_service(name: str, url: str, max_wait: int = 120, interval: int = 3) -> None:
    """Poll a URL until it returns HTTP 200 or timeout expires."""
    deadline = time.time() + max_wait
    logger.info("Waiting for %s at %s ...", name, url)
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code < 500:
                logger.info("%s is ready.", name)
                return
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for {name} at {url} after {max_wait}s")


def wait_for_dependencies() -> None:
    _wait_for_service("Qdrant", f"{QDRANT_URL}/readyz")
    _wait_for_service("Ollama", f"{OLLAMA_BASE_URL}/api/tags")


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------

def _load_jsonl_files(path_value: str) -> list[dict[str, Any]]:
    path_text = path_value.strip()
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        logger.warning("JSONL path not found: %s", path)
        return []
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    raw_docs: list[dict[str, Any]] = []
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        doc = json.loads(line)
                        if isinstance(doc, dict):
                            raw_docs.append(doc)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("Failed reading %s: %s", file_path, exc)
    return raw_docs


def _normalise_evidence(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for i, payload in enumerate(raw):
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        source_path = str(payload.get("source_path") or "")
        docs.append(
            {
                "id": payload.get("id") or payload.get("chunk_id") or f"evidence:{i}",
                "content": content,
                "source_name": Path(source_path).name if source_path else "",
                "source_path": source_path,
                "corpus": payload.get("corpus") or "b",
                "corpus_role": payload.get("corpus_role") or "narrative_guidance",
                "upload_source": payload.get("upload_source") or "local",
                "uploaded_by": payload.get("uploaded_by") or "local",
                "upload_batch": payload.get("upload_batch") or "local",
                "uploaded_at": payload.get("uploaded_at") or "",
                "original_filename": payload.get("original_filename") or Path(source_path).name,
                "content_sha256": payload.get("content_sha256") or "",
                "normalised_text_sha256": payload.get("normalised_text_sha256") or "",
                "dedupe_hash": payload.get("dedupe_hash") or "",
                "dedupe_method": payload.get("dedupe_method") or "",
            }
        )
    return docs


def _normalise_controls(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for payload in raw:
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
                "source_uri": payload.get("source_uri") or "",
            }
        )
    return docs


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_text(text: str) -> list[float]:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBEDDING_MODEL, "prompt": text},
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    if isinstance(body.get("embedding"), list):
        return [float(v) for v in body["embedding"]]
    if isinstance(body.get("embeddings"), list) and body["embeddings"]:
        return [float(v) for v in body["embeddings"][0]]
    raise RuntimeError(f"Unexpected embedding response: {list(body.keys())}")


def _text_for_embedding(doc: dict[str, Any]) -> str:
    return str(
        doc.get("content") or doc.get("requirement_text") or doc.get("guidance_text") or ""
    ).strip()


def _point_id(doc: dict[str, Any], ordinal: int, collection: str) -> int:
    seed = str(doc.get("id") or doc.get("requirement_id") or f"{collection}:{ordinal}")
    return int(hashlib.sha256(seed.encode()).hexdigest()[:15], 16)


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

def _collection_count(collection: str) -> int | None:
    """Return number of indexed vectors, or None if collection does not exist."""
    try:
        r = requests.get(f"{QDRANT_URL}/collections/{collection}", timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        info = r.json()
        return info.get("result", {}).get("vectors_count", 0)
    except Exception as exc:
        logger.warning("Could not read collection %s: %s", collection, exc)
        return None


def _delete_collection(collection: str) -> None:
    requests.delete(f"{QDRANT_URL}/collections/{collection}", timeout=10)


def _create_collection(collection: str, dim: int) -> None:
    payload = {
        "vectors": {
            "size": dim,
            "distance": "Cosine",
        }
    }
    r = requests.put(
        f"{QDRANT_URL}/collections/{collection}",
        json=payload,
        timeout=10,
    )
    r.raise_for_status()


def _upsert_points(collection: str, points: list[dict[str, Any]]) -> None:
    BATCH_SIZE = 64
    total = len(points)
    for start in range(0, total, BATCH_SIZE):
        batch = points[start : start + BATCH_SIZE]
        r = requests.put(
            f"{QDRANT_URL}/collections/{collection}/points",
            json={"points": batch},
            timeout=120,
        )
        r.raise_for_status()
        logger.info(
            "  Upserted %d/%d vectors into '%s'",
            min(start + BATCH_SIZE, total),
            total,
            collection,
        )


# ---------------------------------------------------------------------------
# Seed pipeline
# ---------------------------------------------------------------------------

def seed_collection(
    collection: str,
    docs: list[dict[str, Any]],
    *,
    force: bool = False,
) -> int:
    """Embed and upsert docs into a Qdrant collection. Returns count of seeded docs."""
    existing = _collection_count(collection)
    if existing is not None and existing > 0 and not force:
        logger.info(
            "Collection '%s' already has %d vectors — skipping. Use --force to re-seed.",
            collection,
            existing,
        )
        return existing

    if not docs:
        logger.warning("No documents to seed into '%s'. Skipping.", collection)
        return 0

    logger.info("Embedding %d documents for '%s' using %s ...", len(docs), collection, EMBEDDING_MODEL)

    vectors: list[list[float]] = []
    payload_docs: list[dict[str, Any]] = []
    for i, doc in enumerate(docs):
        text = _text_for_embedding(doc)
        if not text:
            continue
        vec = _embed_text(text)
        vectors.append(vec)
        payload_docs.append(doc)
        if (i + 1) % 50 == 0:
            logger.info("  Embedded %d/%d ...", i + 1, len(docs))

    if not vectors:
        logger.warning("No embeddable text found in documents for '%s'.", collection)
        return 0

    dim = len(vectors[0])
    logger.info("Dimension: %d  Docs to index: %d", dim, len(payload_docs))

    if existing is not None:
        logger.info("Dropping existing collection '%s'.", collection)
        _delete_collection(collection)
    _create_collection(collection, dim)

    points = [
        {
            "id": _point_id(doc, i, collection),
            "vector": vec,
            "payload": doc,
        }
        for i, (doc, vec) in enumerate(zip(payload_docs, vectors))
    ]
    _upsert_points(collection, points)
    logger.info("Seeded %d vectors into '%s'.", len(points), collection)
    return len(points)


# ---------------------------------------------------------------------------
# Check mode
# ---------------------------------------------------------------------------

def check_indexes() -> dict[str, int | None]:
    counts: dict[str, int | None] = {
        EVIDENCE_INDEX: _collection_count(EVIDENCE_INDEX),
        CONTROLS_INDEX: _collection_count(CONTROLS_INDEX),
    }
    for name, count in counts.items():
        if count is None:
            logger.warning("Collection '%s': MISSING", name)
        else:
            logger.info("Collection '%s': %d vectors", name, count)
    return counts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Seed local Qdrant indexes from JSONL files.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report collection counts and exit non-zero if any are empty or missing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop and re-seed existing collections.",
    )
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help="Only seed the evidence/grounding index.",
    )
    parser.add_argument(
        "--controls-only",
        action="store_true",
        help="Only seed the controls index.",
    )
    args = parser.parse_args()

    logger.info("=== Local index seeder ===")
    logger.info("  Qdrant:          %s", QDRANT_URL)
    logger.info("  Ollama:          %s", OLLAMA_BASE_URL)
    logger.info("  Embedding model: %s", EMBEDDING_MODEL)
    logger.info("  Evidence path:   %s", EVIDENCE_PATH)
    logger.info("  Controls path:   %s", CONTROLS_PATH)

    wait_for_dependencies()

    if args.check:
        counts = check_indexes()
        missing_or_empty = [k for k, v in counts.items() if not v]
        if missing_or_empty:
            logger.error("Collections not ready: %s", missing_or_empty)
            return 1
        logger.info("All indexes are populated.")
        return 0

    seed_evidence = not args.controls_only
    seed_controls = not args.evidence_only
    total_seeded = 0

    if seed_evidence:
        logger.info("--- Seeding evidence index: %s ---", EVIDENCE_INDEX)
        raw = _load_jsonl_files(EVIDENCE_PATH)
        logger.info("Loaded %d raw evidence documents from %s", len(raw), EVIDENCE_PATH)
        docs = _normalise_evidence(raw)
        logger.info("Normalised to %d embeddable evidence documents", len(docs))
        total_seeded += seed_collection(EVIDENCE_INDEX, docs, force=args.force)

    if seed_controls:
        logger.info("--- Seeding controls index: %s ---", CONTROLS_INDEX)
        raw = _load_jsonl_files(CONTROLS_PATH)
        logger.info("Loaded %d raw control documents from %s", len(raw), CONTROLS_PATH)
        docs = _normalise_controls(raw)
        logger.info("Normalised to %d embeddable control documents", len(docs))
        total_seeded += seed_collection(CONTROLS_INDEX, docs, force=args.force)

    logger.info("=== Seeding complete. Total vectors indexed: %d ===", total_seeded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
