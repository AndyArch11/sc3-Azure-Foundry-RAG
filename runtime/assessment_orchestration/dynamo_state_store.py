"""DynamoDB-backed polling state store.

Table layout
------------
Each item in the table uses a two-attribute primary key:

  PK  (partition key) : source         – e.g. "confluence"
  SK  (sort key)      : doc_key        – e.g. "state", "lock", "processed:abc123"

A single DynamoDB table therefore replaces the single Cosmos container.  All
operations are scoped to the (source, doc_key) pair, mirroring the Cosmos
partition_key=source + item id pattern.

TTL
---
Items that should expire (processed events, leases) carry a ``ttl_epoch``
attribute.  Enable DynamoDB TTL on that attribute in the AWS Console or
Terraform so items are automatically removed.

Concurrency
-----------
``commit_state`` uses a conditional expression on a ``version`` counter to
implement optimistic concurrency, matching the Cosmos ``_etag`` behaviour.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
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


class DynamoDBPollingStateStore:
    """PollingStateStore backed by a single AWS DynamoDB table.

    Parameters
    ----------
    table_name:
        DynamoDB table name.  Falls back to the ``DYNAMODB_TABLE`` env var.
    session:
        A ``boto3.Session`` (or compatible).  When *None* a default session is
        created via ``boto3.Session()``.
    region_name:
        AWS region for the DynamoDB client.  Ignored when *session* is provided.
    """

    def __init__(
        self,
        table_name: str | None = None,
        *,
        session: Any = None,
        region_name: str | None = None,
    ) -> None:
        self._table_name = table_name or os.getenv("DYNAMODB_TABLE", "")
        if not self._table_name:
            raise ValueError("table_name must be supplied or DYNAMODB_TABLE env var must be set")
        if session is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for DynamoDBPollingStateStore but is not installed"
                ) from exc
            session = boto3.Session(region_name=region_name)
        self._dynamo = session.resource("dynamodb")
        self._table = self._dynamo.Table(self._table_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, source: str, doc_key: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={"source": source, "doc_key": doc_key})
        return resp.get("Item")

    def _put(self, item: dict[str, Any]) -> dict[str, Any]:
        self._table.put_item(Item=item)
        return item

    def _update(
        self,
        source: str,
        doc_key: str,
        updates: dict[str, Any],
        condition_expr: str | None = None,
        expr_names: dict[str, str] | None = None,
        expr_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        set_parts = [f"#{k} = :{k}" for k in updates]
        update_expr = "SET " + ", ".join(set_parts)

        names: dict[str, str] = {f"#{k}": k for k in updates}
        values: dict[str, Any] = {f":{k}": v for k, v in updates.items()}

        if expr_names:
            names.update(expr_names)
        if expr_values:
            values.update(expr_values)

        kwargs: dict[str, Any] = {
            "Key": {"source": source, "doc_key": doc_key},
            "UpdateExpression": update_expr,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if condition_expr:
            kwargs["ConditionExpression"] = condition_expr

        resp = self._table.update_item(**kwargs)
        return resp.get("Attributes") or {}

    # ------------------------------------------------------------------
    # PollingState
    # ------------------------------------------------------------------

    def load_state(self, source: str) -> PollingState:
        payload = self._get(source, "state") or {"source": source, "poll_count": 0}
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
        current = self._get(source, "state") or {
            "source": source,
            "doc_key": "state",
            "poll_count": 0,
            "version": 0,
        }
        current_version = int(current.get("version") or 0)
        new_version = current_version + 1

        updates: dict[str, Any] = {
            "source": source,
            "doc_key": "state",
            "watermark": watermark,
            "last_processed_event_id": last_processed_event_id,
            "last_error": last_error or {},
            "last_success_at": _utc_now_iso(),
            "poll_count": int(current.get("poll_count") or 0) + max(0, poll_count_increment),
            "version": new_version,
            # Expose version as _etag so _coerce_state picks it up
            "_etag": str(new_version),
        }

        if expected_etag:
            try:
                attrs = self._update(
                    source,
                    "state",
                    updates,
                    condition_expr="#version = :expected_version",
                    expr_names={"#version": "version"},
                    expr_values={":expected_version": int(expected_etag)},
                )
            except Exception as exc:
                # ConditionalCheckFailedException → etag mismatch
                if "ConditionalCheckFailed" in type(exc).__name__:
                    raise RuntimeError(f"commit_state etag mismatch for source '{source}'") from exc
                raise
        else:
            updates["version"] = new_version
            self._put({**current, **updates})
            attrs = {**current, **updates}

        return _coerce_state(attrs)

    # ------------------------------------------------------------------
    # Lease / distributed lock
    # ------------------------------------------------------------------

    def try_acquire_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        ttl_epoch = int(now.timestamp()) + ttl_seconds
        current = self._get(source, "lock")

        if current:
            expires_raw = str(current.get("lease_expires_at") or "")
            if expires_raw:
                try:
                    if (
                        _parse_iso(expires_raw) > now
                        and str(current.get("owner_run_id") or "") != owner_run_id
                    ):
                        return False
                except Exception:
                    pass

        self._put(
            {
                "source": source,
                "doc_key": "lock",
                "owner_run_id": owner_run_id,
                "lease_expires_at": expires_at,
                "heartbeat_at": now.isoformat(),
                "ttl_epoch": ttl_epoch,
            }
        )
        return True

    def renew_lease(self, source: str, *, owner_run_id: str, ttl_seconds: int) -> bool:
        current = self._get(source, "lock")
        if not current or str(current.get("owner_run_id") or "") != owner_run_id:
            return False
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        ttl_epoch = int(now.timestamp()) + ttl_seconds
        self._update(
            source,
            "lock",
            {
                "lease_expires_at": expires_at,
                "heartbeat_at": now.isoformat(),
                "ttl_epoch": ttl_epoch,
            },
        )
        return True

    def release_lease(self, source: str, *, owner_run_id: str) -> None:
        current = self._get(source, "lock")
        if current and str(current.get("owner_run_id") or "") == owner_run_id:
            self._table.delete_item(Key={"source": source, "doc_key": "lock"})

    # ------------------------------------------------------------------
    # Processed event deduplication
    # ------------------------------------------------------------------

    def is_event_processed(self, source: str, event_id: str) -> bool:
        doc_key = f"processed:{event_id}"
        item = self._get(source, doc_key)
        if not item:
            return False
        # Check TTL ourselves in case DynamoDB TTL sweep hasn't run yet
        ttl_epoch = item.get("ttl_epoch")
        if ttl_epoch and int(ttl_epoch) <= int(time.time()):
            return False
        return True

    def mark_processed_event(
        self, source: str, *, event_id: str, run_id: str, ttl_hours: int = 48
    ) -> None:
        ttl_epoch = int(time.time()) + ttl_hours * 3600
        self._put(
            {
                "source": source,
                "doc_key": f"processed:{event_id}",
                "event_id": event_id,
                "run_id": run_id,
                "processed_at": _utc_now_iso(),
                "ttl_epoch": ttl_epoch,
            }
        )

    # ------------------------------------------------------------------
    # Failure tracking
    # ------------------------------------------------------------------

    def increment_failure_count(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> int:
        doc_key = f"failure:{event_id}"
        current = self._get(source, doc_key) or {
            "source": source,
            "doc_key": doc_key,
            "event_id": event_id,
            "attempt_count": 0,
            "status": "pending",
        }
        new_count = int(current.get("attempt_count") or 0) + 1
        current.update(
            {
                "attempt_count": new_count,
                "status": "failed_retryable",
                "last_error": error_message,
                "last_attempt_at": _utc_now_iso(),
                "run_id": run_id,
            }
        )
        self._put(current)
        return new_count

    def mark_terminal_failure(
        self, source: str, *, event_id: str, error_message: str, run_id: str
    ) -> None:
        doc_key = f"failure:{event_id}"
        current = self._get(source, doc_key) or {
            "source": source,
            "doc_key": doc_key,
            "event_id": event_id,
            "attempt_count": 0,
        }
        current.update(
            {
                "status": "failed_terminal",
                "last_error": error_message,
                "last_attempt_at": _utc_now_iso(),
                "run_id": run_id,
            }
        )
        self._put(current)

    # ------------------------------------------------------------------
    # Assessment snapshots
    # ------------------------------------------------------------------

    def get_assessment_snapshot(
        self, source: str, *, target_id: str, framework_scope: str
    ) -> AssessmentSnapshot | None:
        doc_key = f"assessment:{target_id}:{framework_scope}"
        payload = self._get(source, doc_key)
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
        doc_key = f"assessment:{target_id}:{framework_scope}"
        payload = {
            "source": source,
            "doc_key": doc_key,
            "target_id": target_id,
            "framework_scope": framework_scope,
            "page_version": page_version,
            "content_hash": content_hash,
            "updated_at": _utc_now_iso(),
        }
        self._put(payload)
        return AssessmentSnapshot(
            source=source,
            target_id=target_id,
            framework_scope=framework_scope,
            page_version=page_version,
            content_hash=content_hash,
            updated_at=str(payload["updated_at"]),
        )

    # ------------------------------------------------------------------
    # Poll run summary
    # ------------------------------------------------------------------

    def get_latest_poll_run_summary(self, source: str) -> PollRunSummary | None:
        payload = self._get(source, "poll_run_summary")
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
            "doc_key": "poll_run_summary",
            "polled_at": polled_at,
            "since_iso": since_iso,
            "watermark": watermark,
            "mentions_found": int(max(0, mentions_found)),
            "jobs_queued": int(max(0, jobs_queued)),
            "terminal_failures": int(max(0, terminal_failures)),
            "error_message": error_message,
            "space_keys": list(space_keys),
        }
        self._put(payload)
        return PollRunSummary(
            source=source,
            polled_at=polled_at,
            since_iso=since_iso,
            watermark=watermark,
            mentions_found=int(max(0, mentions_found)),
            jobs_queued=int(max(0, jobs_queued)),
            terminal_failures=int(max(0, terminal_failures)),
            error_message=error_message,
            space_keys=space_keys,
        )

    # ------------------------------------------------------------------
    # Page assessments
    # ------------------------------------------------------------------

    def list_recent_page_assessments(
        self, source: str, *, since_iso: str, limit: int = 100
    ) -> list[PageAssessmentRecord]:
        from boto3.dynamodb.conditions import Attr, Key

        try:
            resp = self._table.query(
                KeyConditionExpression=Key("source").eq(source)
                & Key("doc_key").begins_with("page_assessment:"),
                FilterExpression=Attr("assessed_at").gte(since_iso),
                Limit=max(1, limit) * 4,  # over-fetch to compensate for filter
            )
            items: list[dict[str, Any]] = resp.get("Items") or []
        except Exception:
            logger.warning("list_recent_page_assessments query failed", exc_info=True)
            return []

        records: list[PageAssessmentRecord] = []
        for payload in items:
            assessed_at = str(payload.get("assessed_at") or "")
            if not assessed_at or assessed_at < since_iso:
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
        doc_key = f"page_assessment:{target_id}:{framework_scope}"
        payload = {
            "source": source,
            "doc_key": doc_key,
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
        self._put(payload)
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

    # ------------------------------------------------------------------
    # Failure listing
    # ------------------------------------------------------------------

    def list_recent_failures(
        self, source: str, *, since_iso: str, limit: int = 50
    ) -> list[FailureRecord]:
        from boto3.dynamodb.conditions import Attr, Key

        try:
            resp = self._table.query(
                KeyConditionExpression=Key("source").eq(source)
                & Key("doc_key").begins_with("failure:"),
                FilterExpression=Attr("last_attempt_at").gte(since_iso),
                Limit=max(1, limit) * 4,
            )
            items = resp.get("Items") or []
        except Exception:
            logger.warning("list_recent_failures query failed", exc_info=True)
            return []

        records: list[FailureRecord] = []
        for payload in items:
            last_attempt_at = str(payload.get("last_attempt_at") or "")
            if not last_attempt_at or last_attempt_at < since_iso:
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
