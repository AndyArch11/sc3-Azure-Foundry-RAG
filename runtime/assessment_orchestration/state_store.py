from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    clean = value.replace("Z", "+00:00")
    return datetime.fromisoformat(clean)


def _coerce_state(payload: dict[str, Any]) -> "PollingState":
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
    source: str
    watermark: str = ""
    last_success_at: str = ""
    poll_count: int = 0
    last_processed_event_id: str = ""
    last_error: dict[str, Any] | None = None
    etag: str = ""


class PollingStateStore(Protocol):
    def load_state(self, source: str) -> PollingState:
        ...

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
        ...

    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        ...

    def renew_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        ...

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        ...

    def is_event_processed(self, source: str, event_id: str) -> bool:
        ...

    def mark_processed_event(self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48) -> None:
        ...

    def increment_failure_count(self, source: str, *, event_id: str, error_message: str, run_id: str) -> int:
        ...

    def mark_terminal_failure(self, source: str, *, event_id: str, error_message: str, run_id: str) -> None:
        ...


class InMemoryPollingStateStore:
    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}
        self._lease: dict[str, dict[str, Any]] = {}
        self._processed: dict[tuple[str, str], dict[str, Any]] = {}
        self._failures: dict[tuple[str, str], dict[str, Any]] = {}

    def load_state(self, source: str) -> PollingState:
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
        now = datetime.now(UTC)
        current = self._lease.get(source)
        if not current or str(current.get("owner_run_id") or "") != owner_run_id:
            return False
        current["lease_expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
        current["heartbeat_at"] = now.isoformat()
        return True

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        current = self._lease.get(source)
        if current and str(current.get("owner_run_id") or "") == owner_run_id:
            del self._lease[source]

    def is_event_processed(self, source: str, event_id: str) -> bool:
        key = (source, event_id)
        row = self._processed.get(key)
        if not row:
            return False
        expires_raw = str(row.get("expires_at") or "")
        if expires_raw and _parse_iso(expires_raw) <= datetime.now(UTC):
            del self._processed[key]
            return False
        return True

    def mark_processed_event(self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48) -> None:
        self._processed[(source, event_id)] = {
            "source": source,
            "event_id": event_id,
            "run_id": run_id,
            "processed_at": _utc_now_iso(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat(),
        }

    def increment_failure_count(self, source: str, *, event_id: str, error_message: str, run_id: str) -> int:
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

    def mark_terminal_failure(self, source: str, *, event_id: str, error_message: str, run_id: str) -> None:
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


class CosmosPollingStateStore:
    """Cosmos-backed state store using one container keyed by /source.

    Document ids are namespaced by source and type:
      - {source}:state
      - {source}:lock
      - {source}:processed:{event_id}
      - {source}:failure:{event_id}
    """

    def __init__(self, container_client: Any) -> None:
        self._container = container_client

    def _state_id(self, source: str) -> str:
        return f"{source}:state"

    def _lock_id(self, source: str) -> str:
        return f"{source}:lock"

    def _processed_id(self, source: str, event_id: str) -> str:
        return f"{source}:processed:{event_id}"

    def _failure_id(self, source: str, event_id: str) -> str:
        return f"{source}:failure:{event_id}"

    def _read(self, source: str, doc_id: str) -> dict[str, Any] | None:
        try:
            return self._container.read_item(item=doc_id, partition_key=source)
        except Exception:
            return None

    def _upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._container.upsert_item(payload)

    def load_state(self, source: str) -> PollingState:
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
        now = datetime.now(UTC)
        lock = self._read(source, self._lock_id(source))
        if not lock or str(lock.get("owner_run_id") or "") != owner_run_id:
            return False
        lock["lease_expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
        lock["heartbeat_at"] = now.isoformat()
        self._upsert(lock)
        return True

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        lock = self._read(source, self._lock_id(source))
        if not lock or str(lock.get("owner_run_id") or "") != owner_run_id:
            return
        try:
            self._container.delete_item(item=self._lock_id(source), partition_key=source)
        except Exception:
            return

    def is_event_processed(self, source: str, event_id: str) -> bool:
        doc = self._read(source, self._processed_id(source, event_id))
        return doc is not None

    def mark_processed_event(self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48) -> None:
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

    def increment_failure_count(self, source: str, *, event_id: str, error_message: str, run_id: str) -> int:
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

    def mark_terminal_failure(self, source: str, *, event_id: str, error_message: str, run_id: str) -> None:
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
