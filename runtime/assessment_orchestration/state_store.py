from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Storage schema version stamped on every Cosmos document written by the polling/assessment worker.
# Bump when the document shape changes and follow the rolling migration playbook in
# docs/compliance-rag-recommended-approach.md.
COSMOS_STATE_SCHEMA_VERSION = "v1"

# Identity emitted in cosmos_schema_access log lines.
_SERVICE_NAME = "polling-worker"


def _log_cosmos_access(
    *,
    operation: str,
    container: str,
    schema_version_read: str,
    schema_version_written: str,
    upcasted: bool,
    correlation_id: str = "",
) -> None:
    """Emit a structured cosmos_schema_access log line for schema version monitoring."""
    logger.info(
        "cosmos_schema_access",
        extra={
            "schema_version_read": schema_version_read,
            "schema_version_written": schema_version_written,
            "upcasted": upcasted,
            "client_id": _SERVICE_NAME,
            "operation": operation,
            "container": container,
            "correlation_id": correlation_id,
        },
    )


def _utc_now_iso() -> str:
    """Run utc now iso."""
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    """Run parse iso."""
    clean = value.replace("Z", "+00:00")
    return datetime.fromisoformat(clean)


def _coerce_state(payload: dict[str, Any]) -> "PollingState":
    """Run coerce state."""
    return PollingState(
        source=str(payload.get("source") or "confluence"),
        watermark=str(payload.get("watermark") or ""),
        last_success_at=str(payload.get("last_success_at") or ""),
        poll_count=int(payload.get("poll_count") or 0),
        last_processed_event_id=str(payload.get("last_processed_event_id") or ""),
        last_error=dict(payload.get("last_error") or {}),
        etag=str(payload.get("_etag") or ""),
    )


@dataclass(frozen=True)
class PollingState:
    """PollingState."""

    source: str
    watermark: str = ""
    last_success_at: str = ""
    poll_count: int = 0
    last_processed_event_id: str = ""
    last_error: dict[str, Any] | None = None
    etag: str = ""


@dataclass(frozen=True)
class AssessmentSnapshot:
    """AssessmentSnapshot."""

    source: str
    target_id: str
    framework_scope: str
    page_version: str = ""
    content_hash: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class PollRunSummary:
    """PollRunSummary."""

    source: str
    polled_at: str
    since_iso: str = ""
    watermark: str = ""
    mentions_found: int = 0
    jobs_queued: int = 0
    terminal_failures: int = 0
    error_message: str = ""
    space_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageAssessmentRecord:
    """PageAssessmentRecord."""

    source: str
    target_id: str
    framework_scope: str
    title: str = ""
    target_url: str = ""
    space_key: str = ""
    status: str = "assessed"
    overall_risk: str = "unknown"
    findings_count: int = 0
    assessed_at: str = ""
    page_version: str = ""


@dataclass(frozen=True)
class FailureRecord:
    """FailureRecord."""

    source: str
    event_id: str
    status: str
    attempt_count: int = 0
    last_error: str = ""
    last_attempt_at: str = ""
    run_id: str = ""


class PollingStateStore(Protocol):
    """PollingStateStore."""

    def load_state(self, source: str) -> PollingState: ...

    def commit_state(
        self,
        source: str,
        *,
        watermark: str,
        last_processed_event_id: str = "",
        last_error: dict[str, Any] | None = None,
        poll_count_increment: int = 0,
        expected_etag: str = "",
    ) -> PollingState: ...

    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool: ...

    def renew_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool: ...

    def release_lease(self, source: str, *, owner_run_id: str) -> None: ...

    def is_event_processed(self, source: str, event_id: str) -> bool: ...

    def mark_processed_event(
        self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48
    ) -> None: ...

    def increment_failure_count(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> int: ...

    def mark_terminal_failure(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> None: ...

    def get_assessment_snapshot(
        self, source: str, *, target_id: str, framework_scope: str
    ) -> AssessmentSnapshot | None: ...

    def upsert_assessment_snapshot(
        self,
        source: str,
        *,
        target_id: str,
        framework_scope: str,
        page_version: str,
        content_hash: str,
    ) -> AssessmentSnapshot: ...

    def get_latest_poll_run_summary(self, source: str) -> PollRunSummary | None: ...

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
    ) -> PollRunSummary: ...

    def list_recent_page_assessments(
        self, source: str, *, since_iso: str, limit: int = 100
    ) -> list[PageAssessmentRecord]: ...

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
    ) -> PageAssessmentRecord: ...

    def list_recent_failures(
        self, source: str, *, since_iso: str, limit: int = 50
    ) -> list[FailureRecord]: ...


class InMemoryPollingStateStore:
    """InMemoryPollingStateStore."""

    def __init__(self) -> None:
        """Run init."""
        self._state: dict[str, dict[str, Any]] = {}
        self._lease: dict[str, dict[str, Any]] = {}
        self._processed: dict[tuple[str, str], dict[str, Any]] = {}
        self._failures: dict[tuple[str, str], dict[str, Any]] = {}
        self._assessment_snapshots: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._poll_runs: dict[str, dict[str, Any]] = {}
        self._page_assessments: dict[tuple[str, str, str], dict[str, Any]] = {}

    def load_state(self, source: str) -> PollingState:
        """Run load state."""
        payload = self._state.get(source) or {"source": source}
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
        """Run commit state."""
        current = self._state.get(source) or {"source": source, "poll_count": 0}
        current["watermark"] = watermark
        current["last_processed_event_id"] = last_processed_event_id
        current["last_error"] = dict(last_error or {})
        current["last_success_at"] = _utc_now_iso()
        current["poll_count"] = int(current.get("poll_count") or 0) + max(0, poll_count_increment)
        current["_etag"] = str(int(current.get("_etag") or 0) + 1)
        self._state[source] = current
        return _coerce_state(current)

    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        """Run try acquire lease."""
        now = datetime.now(UTC)
        current = self._lease.get(source)
        if current:
            expires_raw = str(current.get("lease_expires_at") or "")
            if expires_raw:
                expires = _parse_iso(expires_raw)
                if expires > now and str(current.get("owner_run_id") or "") != owner_run_id:
                    return False
        self._lease[source] = {
            "source": source,
            "owner_run_id": owner_run_id,
            "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        return True

    def renew_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        """Run renew lease."""
        now = datetime.now(UTC)
        current = self._lease.get(source)
        if not current or str(current.get("owner_run_id") or "") != owner_run_id:
            return False
        current["lease_expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
        current["heartbeat_at"] = now.isoformat()
        return True

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        """Run release lease."""
        current = self._lease.get(source)
        if current and str(current.get("owner_run_id") or "") == owner_run_id:
            del self._lease[source]

    def is_event_processed(self, source: str, event_id: str) -> bool:
        """Run is event processed."""
        key = (source, event_id)
        row = self._processed.get(key)
        if not row:
            return False
        expires_raw = str(row.get("expires_at") or "")
        if expires_raw and _parse_iso(expires_raw) <= datetime.now(UTC):
            del self._processed[key]
            return False
        return True

    def mark_processed_event(
        self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48
    ) -> None:
        """Run mark processed event."""
        self._processed[(source, event_id)] = {
            "source": source,
            "event_id": event_id,
            "run_id": run_id,
            "processed_at": _utc_now_iso(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat(),
        }

    def increment_failure_count(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> int:
        """Run increment failure count."""
        key = (source, event_id)
        row = self._failures.get(key) or {
            "source": source,
            "event_id": event_id,
            "attempt_count": 0,
            "status": "pending",
        }
        row["attempt_count"] = int(row.get("attempt_count") or 0) + 1
        row["status"] = "failed_retryable"
        row["last_error"] = error_message
        row["last_attempt_at"] = _utc_now_iso()
        row["run_id"] = run_id
        self._failures[key] = row
        return int(row["attempt_count"])

    def mark_terminal_failure(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> None:
        """Run mark terminal failure."""
        key = (source, event_id)
        row = self._failures.get(key) or {
            "source": source,
            "event_id": event_id,
            "attempt_count": 0,
        }
        row["status"] = "failed_terminal"
        row["last_error"] = error_message
        row["last_attempt_at"] = _utc_now_iso()
        row["run_id"] = run_id
        self._failures[key] = row

    def get_assessment_snapshot(
        self, source: str, *, target_id: str, framework_scope: str
    ) -> AssessmentSnapshot | None:
        """Run get assessment snapshot."""
        key = (source, target_id, framework_scope)
        payload = self._assessment_snapshots.get(key)
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
        """Run upsert assessment snapshot."""
        payload = {
            "source": source,
            "target_id": target_id,
            "framework_scope": framework_scope,
            "page_version": page_version,
            "content_hash": content_hash,
            "updated_at": _utc_now_iso(),
        }
        self._assessment_snapshots[(source, target_id, framework_scope)] = payload
        return AssessmentSnapshot(**payload)

    def get_latest_poll_run_summary(self, source: str) -> PollRunSummary | None:
        """Run get latest poll run summary."""
        payload = self._poll_runs.get(source)
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
        """Run upsert poll run summary."""
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
        self._poll_runs[source] = payload
        return self.get_latest_poll_run_summary(source) or PollRunSummary(
            source=source, polled_at=polled_at
        )

    def list_recent_page_assessments(
        self, source: str, *, since_iso: str, limit: int = 100
    ) -> list[PageAssessmentRecord]:
        """Run list recent page assessments."""
        since_dt = _parse_iso(since_iso)
        records: list[PageAssessmentRecord] = []
        for payload in self._page_assessments.values():
            if str(payload.get("source") or "") != source:
                continue
            assessed_at = str(payload.get("assessed_at") or "")
            if not assessed_at:
                continue
            try:
                if _parse_iso(assessed_at) < since_dt:
                    continue
            except Exception:
                continue
            records.append(
                PageAssessmentRecord(
                    source=str(payload.get("source") or source),
                    target_id=str(payload.get("target_id") or ""),
                    framework_scope=str(payload.get("framework_scope") or ""),
                    title=str(payload.get("title") or ""),
                    target_url=str(payload.get("target_url") or ""),
                    space_key=str(payload.get("space_key") or ""),
                    status=str(payload.get("status") or "assessed"),
                    overall_risk=str(payload.get("overall_risk") or "unknown"),
                    findings_count=int(payload.get("findings_count") or 0),
                    assessed_at=assessed_at,
                    page_version=str(payload.get("page_version") or ""),
                )
            )
        records.sort(key=lambda item: item.assessed_at, reverse=True)
        return records[: max(1, limit)]

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
        """Run upsert page assessment."""
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
        self._page_assessments[(source, target_id, framework_scope)] = payload
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
        """Run list recent failures."""
        since_dt = _parse_iso(since_iso)
        records: list[FailureRecord] = []
        for payload in self._failures.values():
            if str(payload.get("source") or "") != source:
                continue
            last_attempt_at = str(payload.get("last_attempt_at") or "")
            if not last_attempt_at:
                continue
            try:
                if _parse_iso(last_attempt_at) < since_dt:
                    continue
            except Exception:
                continue
            records.append(
                FailureRecord(
                    source=str(payload.get("source") or source),
                    event_id=str(payload.get("event_id") or ""),
                    status=str(payload.get("status") or "pending"),
                    attempt_count=int(payload.get("attempt_count") or 0),
                    last_error=str(payload.get("last_error") or ""),
                    last_attempt_at=last_attempt_at,
                    run_id=str(payload.get("run_id") or ""),
                )
            )
        records.sort(key=lambda item: item.last_attempt_at, reverse=True)
        return records[: max(1, limit)]


class CosmosPollingStateStore:
    """Cosmos-backed state store using one container keyed by /source.

    Document ids are namespaced by source and type:
      - {source}:state
      - {source}:lock
      - {source}:processed:{event_id}
      - {source}:failure:{event_id}
    """

    def __init__(self, container_client: Any) -> None:
        """Run init."""
        self._container = container_client
        # container_client.id is the Cosmos container name in the Azure SDK; fall back gracefully.
        self._container_name: str = str(getattr(container_client, "id", "state-store"))

    def _state_id(self, source: str) -> str:
        """Run state id."""
        return f"{source}:state"

    def _lock_id(self, source: str) -> str:
        """Run lock id."""
        return f"{source}:lock"

    def _processed_id(self, source: str, event_id: str) -> str:
        """Run processed id."""
        return f"{source}:processed:{event_id}"

    def _failure_id(self, source: str, event_id: str) -> str:
        """Run failure id."""
        return f"{source}:failure:{event_id}"

    def _assessment_snapshot_id(self, source: str, target_id: str, framework_scope: str) -> str:
        """Run assessment snapshot id."""
        return f"{source}:assessment:{target_id}:{framework_scope}"

    def _poll_run_summary_id(self, source: str) -> str:
        """Run poll run summary id."""
        return f"{source}:poll_run_summary"

    def _page_assessment_id(self, source: str, target_id: str, framework_scope: str) -> str:
        """Run page assessment id."""
        return f"{source}:page_assessment:{target_id}:{framework_scope}"

    def _read(self, source: str, doc_id: str) -> dict[str, Any] | None:
        """Run read."""
        try:
            doc = self._container.read_item(item=doc_id, partition_key=source)
            doc_schema = str(doc.get("schema_version") or "unknown")
            upcasted = doc_schema != COSMOS_STATE_SCHEMA_VERSION
            _log_cosmos_access(
                operation="read",
                container=self._container_name,
                schema_version_read=doc_schema,
                schema_version_written="",
                upcasted=upcasted,
            )
            return doc
        except Exception:
            return None

    def _upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run upsert."""
        payload.setdefault("schema_version", COSMOS_STATE_SCHEMA_VERSION)
        result = self._container.upsert_item(payload)
        _log_cosmos_access(
            operation="upsert",
            container=self._container_name,
            schema_version_read="",
            schema_version_written=COSMOS_STATE_SCHEMA_VERSION,
            upcasted=False,
        )
        return result

    def load_state(self, source: str) -> PollingState:
        """Run load state."""
        payload = self._read(source, self._state_id(source)) or {
            "id": self._state_id(source),
            "doc_type": "state",
            "source": source,
            "poll_count": 0,
        }
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
        """Run commit state."""
        current = self._read(source, self._state_id(source)) or {
            "id": self._state_id(source),
            "doc_type": "state",
            "source": source,
            "poll_count": 0,
        }
        current["watermark"] = watermark
        current["last_processed_event_id"] = last_processed_event_id
        current["last_error"] = dict(last_error or {})
        current["last_success_at"] = _utc_now_iso()
        current["poll_count"] = int(current.get("poll_count") or 0) + max(0, poll_count_increment)
        saved = self._upsert(current)
        return _coerce_state(saved)

    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        """Run try acquire lease."""
        now = datetime.now(UTC)
        lock = self._read(source, self._lock_id(source))
        if lock:
            expires_raw = str(lock.get("lease_expires_at") or "")
            if expires_raw:
                expires = _parse_iso(expires_raw)
                if expires > now and str(lock.get("owner_run_id") or "") != owner_run_id:
                    return False
        payload = {
            "id": self._lock_id(source),
            "doc_type": "lock",
            "source": source,
            "owner_run_id": owner_run_id,
            "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        self._upsert(payload)
        return True

    def renew_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        """Run renew lease."""
        now = datetime.now(UTC)
        lock = self._read(source, self._lock_id(source))
        if not lock or str(lock.get("owner_run_id") or "") != owner_run_id:
            return False
        lock["lease_expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
        lock["heartbeat_at"] = now.isoformat()
        self._upsert(lock)
        return True

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        """Run release lease."""
        lock = self._read(source, self._lock_id(source))
        if not lock or str(lock.get("owner_run_id") or "") != owner_run_id:
            return
        try:
            self._container.delete_item(item=self._lock_id(source), partition_key=source)
        except Exception:
            return

    def is_event_processed(self, source: str, event_id: str) -> bool:
        """Run is event processed."""
        doc = self._read(source, self._processed_id(source, event_id))
        return doc is not None

    def mark_processed_event(
        self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48
    ) -> None:
        """Run mark processed event."""
        payload = {
            "id": self._processed_id(source, event_id),
            "doc_type": "processed",
            "source": source,
            "event_id": event_id,
            "run_id": run_id,
            "processed_at": _utc_now_iso(),
            # Cosmos TTL requires container ttl enabled; value is best-effort metadata here.
            "ttl": int(max(1, ttl_hours) * 3600),
        }
        self._upsert(payload)

    def increment_failure_count(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> int:
        """Run increment failure count."""
        doc = self._read(source, self._failure_id(source, event_id)) or {
            "id": self._failure_id(source, event_id),
            "doc_type": "failure",
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
        saved = self._upsert(doc)
        return int(saved.get("attempt_count") or 0)

    def mark_terminal_failure(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> None:
        """Run mark terminal failure."""
        doc = self._read(source, self._failure_id(source, event_id)) or {
            "id": self._failure_id(source, event_id),
            "doc_type": "failure",
            "source": source,
            "event_id": event_id,
            "attempt_count": 0,
        }
        doc["status"] = "failed_terminal"
        doc["last_error"] = error_message
        doc["last_attempt_at"] = _utc_now_iso()
        doc["run_id"] = run_id
        self._upsert(doc)

    def get_assessment_snapshot(
        self, source: str, *, target_id: str, framework_scope: str
    ) -> AssessmentSnapshot | None:
        """Run get assessment snapshot."""
        doc_id = self._assessment_snapshot_id(source, target_id, framework_scope)
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
        """Run upsert assessment snapshot."""
        payload = {
            "id": self._assessment_snapshot_id(source, target_id, framework_scope),
            "doc_type": "assessment_snapshot",
            "source": source,
            "target_id": target_id,
            "framework_scope": framework_scope,
            "page_version": page_version,
            "content_hash": content_hash,
            "updated_at": _utc_now_iso(),
        }
        saved = self._upsert(payload)
        return AssessmentSnapshot(
            source=str(saved.get("source") or source),
            target_id=str(saved.get("target_id") or target_id),
            framework_scope=str(saved.get("framework_scope") or framework_scope),
            page_version=str(saved.get("page_version") or ""),
            content_hash=str(saved.get("content_hash") or ""),
            updated_at=str(saved.get("updated_at") or ""),
        )

    def get_latest_poll_run_summary(self, source: str) -> PollRunSummary | None:
        """Run get latest poll run summary."""
        payload = self._read(source, self._poll_run_summary_id(source))
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
        """Run upsert poll run summary."""
        payload = {
            "id": self._poll_run_summary_id(source),
            "doc_type": "poll_run_summary",
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
        saved = self._upsert(payload)
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
        """Run list recent page assessments."""
        try:
            query = (
                "SELECT * FROM c WHERE c.source = @source AND c.doc_type = 'page_assessment' "
                "AND c.assessed_at >= @since_iso ORDER BY c.assessed_at DESC"
            )
            items = self._container.query_items(
                query=query,
                parameters=[
                    {"name": "@source", "value": source},
                    {"name": "@since_iso", "value": since_iso},
                ],
                partition_key=source,
                max_item_count=max(1, limit),
            )
        except Exception:
            # Fallback for environments lacking suitable indexes/composite-indexes for ORDER BY.
            try:
                query = (
                    "SELECT * FROM c WHERE c.source = @source AND c.doc_type = 'page_assessment'"
                )
                items = self._container.query_items(
                    query=query,
                    parameters=[{"name": "@source", "value": source}],
                    partition_key=source,
                    max_item_count=max(500, limit),
                )
            except Exception:
                return []
        records: list[PageAssessmentRecord] = []
        for payload in items:
            assessed_at = str(payload.get("assessed_at") or "")
            if assessed_at and assessed_at < since_iso:
                continue
            records.append(
                PageAssessmentRecord(
                    source=str(payload.get("source") or source),
                    target_id=str(payload.get("target_id") or ""),
                    framework_scope=str(payload.get("framework_scope") or ""),
                    title=str(payload.get("title") or ""),
                    target_url=str(payload.get("target_url") or ""),
                    space_key=str(payload.get("space_key") or ""),
                    status=str(payload.get("status") or "assessed"),
                    overall_risk=str(payload.get("overall_risk") or "unknown"),
                    findings_count=int(payload.get("findings_count") or 0),
                    assessed_at=assessed_at,
                    page_version=str(payload.get("page_version") or ""),
                )
            )
        records.sort(key=lambda item: item.assessed_at, reverse=True)
        return records[: max(1, limit)]

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
        """Run upsert page assessment."""
        payload = {
            "id": self._page_assessment_id(source, target_id, framework_scope),
            "doc_type": "page_assessment",
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
        saved = self._upsert(payload)
        return PageAssessmentRecord(
            source=str(saved.get("source") or source),
            target_id=str(saved.get("target_id") or target_id),
            framework_scope=str(saved.get("framework_scope") or framework_scope),
            title=str(saved.get("title") or ""),
            target_url=str(saved.get("target_url") or ""),
            space_key=str(saved.get("space_key") or ""),
            status=str(saved.get("status") or "assessed"),
            overall_risk=str(saved.get("overall_risk") or "unknown"),
            findings_count=int(saved.get("findings_count") or 0),
            assessed_at=str(saved.get("assessed_at") or ""),
            page_version=str(saved.get("page_version") or ""),
        )

    def list_recent_failures(
        self, source: str, *, since_iso: str, limit: int = 50
    ) -> list[FailureRecord]:
        """Run list recent failures."""
        try:
            query = (
                "SELECT * FROM c WHERE c.source = @source AND c.doc_type = 'failure' "
                "AND c.last_attempt_at >= @since_iso ORDER BY c.last_attempt_at DESC"
            )
            items = self._container.query_items(
                query=query,
                parameters=[
                    {"name": "@source", "value": source},
                    {"name": "@since_iso", "value": since_iso},
                ],
                partition_key=source,
                max_item_count=max(1, limit),
            )
        except Exception:
            try:
                query = "SELECT * FROM c WHERE c.source = @source AND c.doc_type = 'failure'"
                items = self._container.query_items(
                    query=query,
                    parameters=[{"name": "@source", "value": source}],
                    partition_key=source,
                    max_item_count=max(500, limit),
                )
            except Exception:
                return []
        records: list[FailureRecord] = []
        for payload in items:
            last_attempt_at = str(payload.get("last_attempt_at") or "")
            if last_attempt_at and last_attempt_at < since_iso:
                continue
            records.append(
                FailureRecord(
                    source=str(payload.get("source") or source),
                    event_id=str(payload.get("event_id") or ""),
                    status=str(payload.get("status") or "pending"),
                    attempt_count=int(payload.get("attempt_count") or 0),
                    last_error=str(payload.get("last_error") or ""),
                    last_attempt_at=last_attempt_at,
                    run_id=str(payload.get("run_id") or ""),
                )
            )
        records.sort(key=lambda item: item.last_attempt_at, reverse=True)
        return records[: max(1, limit)]


class LocalFilePollingStateStore(InMemoryPollingStateStore):
    """JSON-file-backed state store for development and offline testing.

    Persists all state to a single JSON file so it survives process restarts
    while retaining the same semantics as ``InMemoryPollingStateStore``.
    """

    def __init__(self, path: str | None = None) -> None:
        """Run init."""
        import json
        import os
        from pathlib import Path

        super().__init__()
        resolved_path = path if path is not None else os.getenv("LOCAL_STATE_STORE_PATH")
        self._path = Path(resolved_path or "/tmp/local_polling_state.json")
        self._json = json
        self._load()

    def _load(self) -> None:
        """Populate in-memory dicts from the JSON file (if it exists)."""
        if not self._path.exists():
            return
        try:
            raw = self._json.loads(self._path.read_text(encoding="utf-8"))
            self._state = {k: v for k, v in raw.get("state", {}).items()}
            self._lease = {k: v for k, v in raw.get("lease", {}).items()}
            self._processed = {
                tuple(k.split("\x00", 1)): v  # type: ignore[misc]
                for k, v in raw.get("processed", {}).items()
            }
            self._failures = {
                tuple(k.split("\x00", 1)): v  # type: ignore[misc]
                for k, v in raw.get("failures", {}).items()
            }
            self._assessment_snapshots = {
                tuple(k.split("\x00", 2)): v  # type: ignore[misc]
                for k, v in raw.get("assessment_snapshots", {}).items()
            }
            self._poll_runs = {k: v for k, v in raw.get("poll_runs", {}).items()}
            self._page_assessments = {
                tuple(k.split("\x00", 2)): v  # type: ignore[misc]
                for k, v in raw.get("page_assessments", {}).items()
            }
        except Exception:
            pass

    def _flush(self) -> None:
        """Write in-memory state to the JSON file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "state": self._state,
            "lease": self._lease,
            "processed": {"\x00".join(k): v for k, v in self._processed.items()},
            "failures": {"\x00".join(k): v for k, v in self._failures.items()},
            "assessment_snapshots": {
                "\x00".join(k): v for k, v in self._assessment_snapshots.items()
            },
            "poll_runs": self._poll_runs,
            "page_assessments": {"\x00".join(k): v for k, v in self._page_assessments.items()},
        }
        self._path.write_text(self._json.dumps(raw, indent=2), encoding="utf-8")

    def commit_state(self, source: str, **kwargs: Any) -> PollingState:
        """Run commit state."""
        result = super().commit_state(source, **kwargs)
        self._flush()
        return result

    def try_acquire_lease(self, source: str, **kwargs: Any) -> bool:
        """Run try acquire lease."""
        result = super().try_acquire_lease(source, **kwargs)
        self._flush()
        return result

    def renew_lease(self, source: str, **kwargs: Any) -> bool:
        """Run renew lease."""
        result = super().renew_lease(source, **kwargs)
        self._flush()
        return result

    def release_lease(self, source: str, **kwargs: Any) -> None:
        """Run release lease."""
        super().release_lease(source, **kwargs)
        self._flush()

    def mark_processed_event(self, source: str, **kwargs: Any) -> None:
        """Run mark processed event."""
        super().mark_processed_event(source, **kwargs)
        self._flush()

    def increment_failure_count(self, source: str, **kwargs: Any) -> int:
        """Run increment failure count."""
        result = super().increment_failure_count(source, **kwargs)
        self._flush()
        return result

    def mark_terminal_failure(self, source: str, **kwargs: Any) -> None:
        """Run mark terminal failure."""
        super().mark_terminal_failure(source, **kwargs)
        self._flush()

    def upsert_assessment_snapshot(self, source: str, **kwargs: Any) -> AssessmentSnapshot:
        """Run upsert assessment snapshot."""
        result = super().upsert_assessment_snapshot(source, **kwargs)
        self._flush()
        return result

    def upsert_poll_run_summary(self, source: str, **kwargs: Any) -> PollRunSummary:
        """Run upsert poll run summary."""
        result = super().upsert_poll_run_summary(source, **kwargs)
        self._flush()
        return result

    def upsert_page_assessment(self, source: str, **kwargs: Any) -> PageAssessmentRecord:
        """Run upsert page assessment."""
        result = super().upsert_page_assessment(source, **kwargs)
        self._flush()
        return result
