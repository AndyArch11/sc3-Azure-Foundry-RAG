"""SQLite-backed conversation store for local/dev mode.

Provides durable, file-backed conversation persistence as a drop-in
replacement for the Azure Cosmos DB container client used in production.

The class exposes the same ``read_item`` / ``upsert_item`` surface that
``query_web/endpoints/conversations.py`` expects, so no call-site changes
are needed beyond container selection at startup.

Configuration
-------------
``LOCAL_STATE_DB_PATH`` env var — same database file used by
``SqlitePollingStateStore`` so both stores share one SQLite file.
Using ``:memory:`` is allowed but ephemeral (in-process only).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    doc_id        TEXT NOT NULL PRIMARY KEY,
    partition_key TEXT NOT NULL DEFAULT '',
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_partition
    ON conversations (partition_key);
"""


def _default_db_path() -> str:
    return os.environ.get("LOCAL_STATE_DB_PATH", ":memory:")


class SqliteConversationStore:
    """File-backed conversation container compatible with the Cosmos container API.

    Implements ``read_item`` and ``upsert_item`` so it can be passed as
    *container* to ``_load_conversation`` / ``_save_conversation`` without
    those functions needing to know the underlying store.

    ``read_item`` raises ``KeyError`` (not ``CosmosResourceNotFoundError``)
    when a document is absent.  The callers in ``conversations.py`` must
    catch ``KeyError`` in addition to the Cosmos exception — see that module
    for the dual-exception guard.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialise schema if needed."""
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
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        logger.debug("SqliteConversationStore initialised at %s", self._path)

    # ------------------------------------------------------------------
    # Cosmos container compatibility surface
    # ------------------------------------------------------------------

    def read_item(self, *, item: str, partition_key: str) -> dict:  # type: ignore[type-arg]
        """Return the document dict or raise ``KeyError`` if not found."""
        row = self._conn.execute(
            "SELECT data FROM conversations WHERE doc_id=?", (item,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Document not found: {item!r}")
        return json.loads(row[0])

    def upsert_item(self, body: dict) -> dict:  # type: ignore[type-arg]
        """Insert or replace a document.  ``body`` must contain an ``id`` field."""
        doc_id = str(body.get("id") or "")
        if not doc_id:
            raise ValueError("Document must have a non-empty 'id' field")
        partition_key = str(body.get("user_id") or "")
        data = json.dumps(body, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO conversations (doc_id, partition_key, data)
                VALUES (?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    partition_key = excluded.partition_key,
                    data          = excluded.data
                """,
                (doc_id, partition_key, data),
            )
        return body

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict] | None = None,
        partition_key: str | None = None,
        max_item_count: int | None = None,
    ) -> list[dict]:  # type: ignore[type-arg]
        """Return conversations compatible with Cosmos ``query_items`` usage.

        The current app call-site passes ``@user_id`` in *parameters* and expects
        rows ordered by ``updated_at`` descending.
        """
        params = parameters or []
        user_id = ""
        for item in params:
            if str(item.get("name") or "") == "@user_id":
                user_id = str(item.get("value") or "")
                break
        if not user_id and partition_key:
            user_id = str(partition_key)

        if not user_id:
            return []

        rows = self._conn.execute(
            "SELECT data FROM conversations WHERE partition_key=?",
            (user_id,),
        ).fetchall()
        docs: list[dict] = []
        for (raw,) in rows:
            try:
                doc = json.loads(raw)
            except Exception:
                continue
            if str(doc.get("type") or "") != "conversation":
                continue
            docs.append(doc)

        docs.sort(
            key=lambda d: str(d.get("updated_at") or d.get("created_at") or ""),
            reverse=True,
        )
        if max_item_count is not None:
            return docs[: max(0, max_item_count)]
        return docs
