"""SQLite-backed PollingStateStore for local/dev mode.

Provides durable, file-backed state that survives process restarts.
Uses a single ``docs`` table keyed by (source, doc_id).

Configuration
-------------
``LOCAL_STATE_DB_PATH`` env var sets the database file path.
Using ``:memory:`` runs entirely in RAM (ephemeral, same process only).

Thread safety
-------------
The ``sqlite3`` connection is opened with ``check_same_thread=False`` and
protected by a ``threading.Lock`` for write serialisation.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .state_store import (
    AssessmentSnapshot,
    FailureRecord,
    PageAssessmentRecord,
    PollingState,
    PollRunSummary,
    _coerce_state,
    _parse_iso,
    _utc_now_iso,
)

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS docs (
    source   TEXT NOT NULL,
    doc_id   TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    data     TEXT NOT NULL,
    PRIMARY KEY (source, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_docs_source_type
    ON docs (source, doc_type);
CREATE INDEX IF NOT EXISTS idx_docs_expires
    ON docs (expires_at)
    WHERE expires_at != '';
"""


def _default_db_path() -> str:
    return os.environ.get("LOCAL_STATE_DB_PATH", ":memory:")


class SqlitePollingStateStore:
    """File-backed ``PollingStateStore`` using SQLite.

    Drop-in replacement for ``InMemoryPollingStateStore`` that survives
    process restarts when ``LOCAL_STATE_DB_PATH`` points to a file.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialise and create schema if needed."""
        self._path = db_path if db_path is not None else _default_db_path()
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        logger.debug("SqlitePollingStateStore initialised at %s", self._path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self, source: str, doc_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT data FROM docs WHERE source=? AND doc_id=?", (source, doc_id)
        ).fetchone()
        if row is None:
            return None
        doc = json.loads(row[0])
        # Honour per-row TTL
        expires_raw = str(doc.get("expires_at") or "")
        if expires_raw:
            try:
                if _parse_iso(expires_raw) <= datetime.now(UTC):
                    with self._lock:
                        self._conn.execute(
                            "DELETE FROM docs WHERE source=? AND doc_id=?", (source, doc_id)
                        )
                    return None
            except Exception:
                pass
        return doc

    def _upsert(
        self, source: str, doc_id: str, doc_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        expires_at = str(payload.get("expires_at") or "")
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO docs (source, doc_id, doc_type, expires_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, doc_id) DO UPDATE SET
                    doc_type   = excluded.doc_type,
                    expires_at = excluded.expires_at,
                    data       = excluded.data
                """,
                (source, doc_id, doc_type, expires_at, data),
            )
        return payload

    def _list(self, source: str, doc_type: str, since_iso: str, limit: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT data FROM docs WHERE source=? AND doc_type=? ORDER BY rowid DESC LIMIT ?",
            (source, doc_type, max(1, limit) * 4),  # over-fetch then filter
        ).fetchall()
        results: list[dict[str, Any]] = []
        for (raw,) in rows:
            try:
                doc = json.loads(raw)
            except Exception:
                continue
            ts = str(doc.get("assessed_at") or doc.get("last_attempt_at") or "")
            if ts and ts < since_iso:
                continue
            results.append(doc)
        results.sort(
            key=lambda d: str(d.get("assessed_at") or d.get("last_attempt_at") or ""),
            reverse=True,
        )
        return results[: max(1, limit)]

    # ------------------------------------------------------------------
    # PollingStateStore protocol
    # ------------------------------------------------------------------

    def load_state(self, source: str) -> PollingState:
        payload = self._read(source, f"{source}:state") or {"source": source}
        return _coerce_state(payload)

    def commit_state(
        self,
        source: str,
        *,
        watermark: str,
        last_processed_event_id: str = "",
        last_error: dict[str, Any] | None = None,
        poll_count_increment: int = 0,
        expected_etag: str = "",
    ) -> PollingState:
        current = self._read(source, f"{source}:state") or {
            "source": source,
            "poll_count": 0,
        }
        current["watermark"] = watermark
        current["last_processed_event_id"] = last_processed_event_id
        current["last_error"] = dict(last_error or {})
        current["last_success_at"] = _utc_now_iso()
        current["poll_count"] = int(current.get("poll_count") or 0) + max(0, poll_count_increment)
        current["_etag"] = str(int(current.get("_etag") or 0) + 1)
        saved = self._upsert(source, f"{source}:state", "state", current)
        return _coerce_state(saved)

    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        now = datetime.now(UTC)
        lock = self._read(source, f"{source}:lock")
        if lock:
            expires_raw = str(lock.get("lease_expires_at") or "")
            if expires_raw:
                try:
                    expires = _parse_iso(expires_raw)
                    if expires > now and str(lock.get("owner_run_id") or "") != owner_run_id:
                        return False
                except Exception:
                    pass
        payload = {
            "source": source,
            "owner_run_id": owner_run_id,
            "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        self._upsert(source, f"{source}:lock", "lock", payload)
        return True

    def renew_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        now = datetime.now(UTC)
        lock = self._read(source, f"{source}:lock")
        if not lock or str(lock.get("owner_run_id") or "") != owner_run_id:
            return False
        lock["lease_expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
        lock["heartbeat_at"] = now.isoformat()
        self._upsert(source, f"{source}:lock", "lock", lock)
        return True

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        lock = self._read(source, f"{source}:lock")
        if lock and str(lock.get("owner_run_id") or "") == owner_run_id:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM docs WHERE source=? AND doc_id=?",
                    (source, f"{source}:lock"),
                )

    def is_event_processed(self, source: str, event_id: str) -> bool:
        doc_id = f"{source}:processed:{event_id}"
        row = self._read(source, doc_id)
        return row is not None

    def mark_processed_event(
        self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48
    ) -> None:
        doc_id = f"{source}:processed:{event_id}"
        expires_at = (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat()
        payload = {
            "source": source,
            "event_id": event_id,
            "run_id": run_id,
            "processed_at": _utc_now_iso(),
            "expires_at": expires_at,
        }
        self._upsert(source, doc_id, "processed", payload)

    def increment_failure_count(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> int:
        doc_id = f"{source}:failure:{event_id}"
        doc = self._read(source, doc_id) or {
            "source": source,
            "event_id": event_id,
            "attempt_count": 0,
            "status": "pending",
        }
        doc["attempt_count"] = int(doc.get("attempt_count") or 0) + 1
        doc["status"] = "failed_retryable"
        doc["last_error"] = error_message
        doc["last_attempt_at"] = _utc_now_iso()
        doc["run_id"] = run_id
        self._upsert(source, doc_id, "failure", doc)
        return int(doc["attempt_count"])

    def mark_terminal_failure(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> None:
        doc_id = f"{source}:failure:{event_id}"
        doc = self._read(source, doc_id) or {
            "source": source,
            "event_id": event_id,
            "attempt_count": 0,
        }
        doc["status"] = "failed_terminal"
        doc["last_error"] = error_message
        doc["last_attempt_at"] = _utc_now_iso()
        doc["run_id"] = run_id
        self._upsert(source, doc_id, "failure", doc)

    def get_assessment_snapshot(
        self, source: str, *, target_id: str, framework_scope: str
    ) -> AssessmentSnapshot | None:
        doc_id = f"{source}:assessment:{target_id}:{framework_scope}"
        payload = self._read(source, doc_id)
        if payload is None:
            return None
        return AssessmentSnapshot(
            source=str(payload.get("source") or source),
            target_id=str(payload.get("target_id") or target_id),
            framework_scope=str(payload.get("framework_scope") or framework_scope),
            page_version=str(payload.get("page_version") or ""),
            content_hash=str(payload.get("content_hash") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def upsert_assessment_snapshot(
        self,
        source: str,
        *,
        target_id: str,
        framework_scope: str,
        page_version: str,
        content_hash: str,
    ) -> AssessmentSnapshot:
        doc_id = f"{source}:assessment:{target_id}:{framework_scope}"
        payload = {
            "source": source,
            "target_id": target_id,
            "framework_scope": framework_scope,
            "page_version": page_version,
            "content_hash": content_hash,
            "updated_at": _utc_now_iso(),
        }
        saved = self._upsert(source, doc_id, "assessment_snapshot", payload)
        return AssessmentSnapshot(**saved)

    def get_latest_poll_run_summary(self, source: str) -> PollRunSummary | None:
        payload = self._read(source, f"{source}:poll_run_summary")
        if payload is None:
            return None
        return PollRunSummary(
            source=str(payload.get("source") or source),
            polled_at=str(payload.get("polled_at") or ""),
            since_iso=str(payload.get("since_iso") or ""),
            watermark=str(payload.get("watermark") or ""),
            mentions_found=int(payload.get("mentions_found") or 0),
            jobs_queued=int(payload.get("jobs_queued") or 0),
            terminal_failures=int(payload.get("terminal_failures") or 0),
            error_message=str(payload.get("error_message") or ""),
            space_keys=tuple(payload.get("space_keys") or ()),
        )

    def upsert_poll_run_summary(
        self,
        source: str,
        *,
        polled_at: str,
        since_iso: str,
        watermark: str,
        mentions_found: int,
        jobs_queued: int,
        terminal_failures: int,
        error_message: str = "",
        space_keys: tuple[str, ...] = (),
    ) -> PollRunSummary:
        payload = {
            "source": source,
            "polled_at": polled_at,
            "since_iso": since_iso,
            "watermark": watermark,
            "mentions_found": int(max(0, mentions_found)),
            "jobs_queued": int(max(0, jobs_queued)),
            "terminal_failures": int(max(0, terminal_failures)),
            "error_message": error_message,
            "space_keys": list(space_keys),
        }
        saved = self._upsert(source, f"{source}:poll_run_summary", "poll_run_summary", payload)
        return PollRunSummary(
            source=str(saved.get("source") or source),
            polled_at=str(saved.get("polled_at") or ""),
            since_iso=str(saved.get("since_iso") or ""),
            watermark=str(saved.get("watermark") or ""),
            mentions_found=int(saved.get("mentions_found") or 0),
            jobs_queued=int(saved.get("jobs_queued") or 0),
            terminal_failures=int(saved.get("terminal_failures") or 0),
            error_message=str(saved.get("error_message") or ""),
            space_keys=tuple(saved.get("space_keys") or ()),
        )

    def list_recent_page_assessments(
        self, source: str, *, since_iso: str, limit: int = 100
    ) -> list[PageAssessmentRecord]:
        docs = self._list(source, "page_assessment", since_iso, limit)
        return [
            PageAssessmentRecord(
                source=str(d.get("source") or source),
                target_id=str(d.get("target_id") or ""),
                framework_scope=str(d.get("framework_scope") or ""),
                title=str(d.get("title") or ""),
                target_url=str(d.get("target_url") or ""),
                space_key=str(d.get("space_key") or ""),
                status=str(d.get("status") or "assessed"),
                overall_risk=str(d.get("overall_risk") or "unknown"),
                findings_count=int(d.get("findings_count") or 0),
                assessed_at=str(d.get("assessed_at") or ""),
                page_version=str(d.get("page_version") or ""),
            )
            for d in docs
        ]

    def upsert_page_assessment(
        self,
        source: str,
        *,
        target_id: str,
        framework_scope: str,
        title: str,
        target_url: str,
        space_key: str,
        status: str,
        overall_risk: str,
        findings_count: int,
        assessed_at: str,
        page_version: str,
    ) -> PageAssessmentRecord:
        doc_id = f"{source}:page_assessment:{target_id}:{framework_scope}"
        payload = {
            "source": source,
            "target_id": target_id,
            "framework_scope": framework_scope,
            "title": title,
            "target_url": target_url,
            "space_key": space_key,
            "status": status,
            "overall_risk": overall_risk,
            "findings_count": int(max(0, findings_count)),
            "assessed_at": assessed_at,
            "page_version": page_version,
        }
        self._upsert(source, doc_id, "page_assessment", payload)
        return PageAssessmentRecord(
            source=source,
            target_id=target_id,
            framework_scope=framework_scope,
            title=title,
            target_url=target_url,
            space_key=space_key,
            status=status,
            overall_risk=overall_risk,
            findings_count=int(max(0, findings_count)),
            assessed_at=assessed_at,
            page_version=page_version,
        )

    def list_recent_failures(
        self, source: str, *, since_iso: str, limit: int = 50
    ) -> list[FailureRecord]:
        docs = self._list(source, "failure", since_iso, limit)
        return [
            FailureRecord(
                source=str(d.get("source") or source),
                event_id=str(d.get("event_id") or ""),
                status=str(d.get("status") or "pending"),
                attempt_count=int(d.get("attempt_count") or 0),
                last_error=str(d.get("last_error") or ""),
                last_attempt_at=str(d.get("last_attempt_at") or ""),
                run_id=str(d.get("run_id") or ""),
            )
            for d in docs
        ]
