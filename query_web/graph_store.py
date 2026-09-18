"""SQLite-backed local graph store for relationship graph baseline.

- Persist graph nodes and edges locally in SQLite.
- Provide baseline query helpers for node lookup, neighbors, and bounded subgraph.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any, Literal, Sequence

from query_web.graph_artifacts import GraphEdgeRecord, GraphNodeRecord

_DDL = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id TEXT NOT NULL PRIMARY KEY,
    node_type TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    graph_schema_version TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id TEXT NOT NULL PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    evidence_key TEXT NOT NULL DEFAULT '',
    graph_schema_version TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_graph_edges_from_id ON graph_edges (from_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_to_id ON graph_edges (to_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_type ON graph_edges (edge_type);
"""


def _default_db_path() -> str:
    """Resolve local SQLite path from environment or use in-memory.

    Returns:
        The path to the local SQLite database.
    """

    return os.environ.get("LOCAL_STATE_DB_PATH", ":memory:")


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    """Read JSONL file into a list of dictionaries.

    Args:
        path: The path to the JSONL file.

    Returns:
        A list of dictionaries parsed from the JSONL file.
    Raises:
        FileNotFoundError: If the specified file does not exist.
    """

    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            records.append(json.loads(text))
    return records


def _to_node_record(item: GraphNodeRecord | dict[str, Any]) -> GraphNodeRecord:
    """Normalise input into GraphNodeRecord.

    Args:
        item: An instance of GraphNodeRecord or a dictionary representing a node.

    Returns:
        An instance of GraphNodeRecord.
    """

    if isinstance(item, GraphNodeRecord):
        return item
    return GraphNodeRecord.model_validate(item)


def _to_edge_record(item: GraphEdgeRecord | dict[str, Any]) -> GraphEdgeRecord:
    """Normalise input into GraphEdgeRecord.

    Args:
        item: An instance of GraphEdgeRecord or a dictionary representing an edge.

    Returns:
        An instance of GraphEdgeRecord.
    """

    if isinstance(item, GraphEdgeRecord):
        return item
    return GraphEdgeRecord.model_validate(item)


class SqliteGraphStore:
    """SQLite persistence and query helper for local graph operations.

    Attributes:
        _path: The path to the SQLite database file.
        _lock: A threading lock to ensure thread-safe operations.
        _conn: The SQLite connection object.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialise the SQLite graph store.

        Args:
            db_path: The path to the SQLite database file. If None, a default path is used.
        """
        self._path = db_path if db_path is not None else _default_db_path()
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        for stmt in _DDL.strip().split(";"):
            sql = stmt.strip()
            if sql:
                self._conn.execute(sql)

    def replace_graph(
        self,
        *,
        nodes: Sequence[GraphNodeRecord | dict[str, Any]],
        edges: Sequence[GraphEdgeRecord | dict[str, Any]],
    ) -> dict[str, int]:
        """Replace persisted graph contents atomically.

        Args:
            nodes: A sequence of GraphNodeRecord instances or dictionaries representing nodes.
            edges: A sequence of GraphEdgeRecord instances or dictionaries representing edges.

        Returns:
            A dictionary with counts of nodes and edges written.
        Raises:
            sqlite3.DatabaseError: If there is an error during the database transaction.
        """

        node_records = [_to_node_record(item) for item in nodes]
        edge_records = [_to_edge_record(item) for item in edges]

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM graph_edges")
                self._conn.execute("DELETE FROM graph_nodes")

                self._conn.executemany(
                    """
                    INSERT INTO graph_nodes (
                        node_id, node_type, label, graph_schema_version, attributes_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            node.node_id,
                            node.node_type.value,
                            node.label,
                            node.graph_schema_version,
                            json.dumps(node.attributes, sort_keys=True, separators=(",", ":")),
                        )
                        for node in node_records
                    ],
                )

                self._conn.executemany(
                    """
                    INSERT INTO graph_edges (
                        edge_id, from_id, to_id, edge_type, confidence,
                        evidence_key, graph_schema_version, attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            edge.edge_id,
                            edge.from_id,
                            edge.to_id,
                            edge.edge_type.value,
                            float(edge.confidence),
                            edge.evidence_key,
                            edge.graph_schema_version,
                            json.dumps(edge.attributes, sort_keys=True, separators=(",", ":")),
                        )
                        for edge in edge_records
                    ],
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        return {"nodes_written": len(node_records), "edges_written": len(edge_records)}

    def load_artifact_files(self, *, nodes_jsonl: str, edges_jsonl: str) -> dict[str, int]:
        """Load nodes/edges JSONL artifacts and persist into SQLite.

        Args:
            nodes_jsonl: Path to the JSONL file containing node records.
            edges_jsonl: Path to the JSONL file containing edge records.

        Returns:
            A dictionary with counts of nodes and edges written.
        Raises:
            FileNotFoundError: If either of the specified JSONL files does not exist.
        """

        return self.replace_graph(nodes=_read_jsonl(nodes_jsonl), edges=_read_jsonl(edges_jsonl))

    def counts(self) -> dict[str, int]:
        """Return persisted node and edge counts.

        Returns:
            A dictionary with counts of nodes and edges in the graph.
        """

        row_nodes = self._conn.execute("SELECT COUNT(1) AS c FROM graph_nodes").fetchone()
        row_edges = self._conn.execute("SELECT COUNT(1) AS c FROM graph_edges").fetchone()
        return {
            "nodes": int(row_nodes["c"] if row_nodes else 0),
            "edges": int(row_edges["c"] if row_edges else 0),
        }

    def list_nodes(self, *, max_items: int = 10000) -> list[dict[str, Any]]:
        """List nodes in deterministic order.

        Args:
            max_items: Maximum number of nodes to return.

        Returns:
            A list of dictionaries representing nodes.
        """

        max_items = max(1, int(max_items))
        rows = self._conn.execute(
            """
            SELECT node_id, node_type, label, graph_schema_version, attributes_json
            FROM graph_nodes
            ORDER BY node_id ASC
            LIMIT ?
            """,
            (max_items,),
        ).fetchall()
        return [
            {
                "node_id": str(row["node_id"]),
                "node_type": str(row["node_type"]),
                "label": str(row["label"]),
                "graph_schema_version": str(row["graph_schema_version"]),
                "attributes": json.loads(str(row["attributes_json"] or "{}")),
            }
            for row in rows
        ]

    def list_edges(self, *, max_items: int = 10000) -> list[dict[str, Any]]:
        """List edges in deterministic order.

        Args:
            max_items: Maximum number of edges to return.

        Returns:
            A list of dictionaries representing edges.
        """

        max_items = max(1, int(max_items))
        rows = self._conn.execute(
            """
            SELECT edge_id, from_id, to_id, edge_type, confidence, evidence_key,
                   graph_schema_version, attributes_json
            FROM graph_edges
            ORDER BY edge_id ASC
            LIMIT ?
            """,
            (max_items,),
        ).fetchall()
        return [
            {
                "edge_id": str(row["edge_id"]),
                "from_id": str(row["from_id"]),
                "to_id": str(row["to_id"]),
                "edge_type": str(row["edge_type"]),
                "confidence": float(row["confidence"]),
                "evidence_key": str(row["evidence_key"]),
                "graph_schema_version": str(row["graph_schema_version"]),
                "attributes": json.loads(str(row["attributes_json"] or "{}")),
            }
            for row in rows
        ]

    def export_graph(self, *, max_nodes: int = 100000, max_edges: int = 100000) -> dict[str, Any]:
        """Export graph snapshot as JSON-serializable dictionary.

        Args:
            max_nodes: Maximum number of nodes to include in the export.
            max_edges: Maximum number of edges to include in the export.

        Returns:
            A dictionary with nodes and edges.
        """

        nodes = self.list_nodes(max_items=max_nodes)
        edges = self.list_edges(max_items=max_edges)
        return {"nodes": nodes, "edges": edges}

    def get_node(self, *, node_id: str) -> dict[str, Any]:
        """Get node by ID or raise KeyError if missing.

        Args:
            node_id: The ID of the node to retrieve.

        Returns:
            A dictionary representing the node.

        Raises:
            KeyError: If the node is not found.
        """

        row = self._conn.execute(
            """
            SELECT node_id, node_type, label, graph_schema_version, attributes_json
            FROM graph_nodes WHERE node_id=?
            """,
            (node_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Node not found: {node_id!r}")
        return {
            "node_id": str(row["node_id"]),
            "node_type": str(row["node_type"]),
            "label": str(row["label"]),
            "graph_schema_version": str(row["graph_schema_version"]),
            "attributes": json.loads(str(row["attributes_json"] or "{}")),
        }

    def neighbours(
        self,
        *,
        node_id: str,
        direction: Literal["out", "in", "both"] = "both",
        max_items: int = 100,
    ) -> list[dict[str, Any]]:
        """Return adjacent edges for a node with direction filter.

        Args:
            node_id: The ID of the node to find neighbours for.
            direction: The direction of edges to consider ("out", "in", or "both").
            max_items: Maximum number of edges to return.

        Returns:
            A list of dictionaries representing edges adjacent to the specified node.
        """

        max_items = max(1, int(max_items))
        params: tuple[Any, ...]
        if direction == "out":
            sql = (
                "SELECT edge_id, from_id, to_id, edge_type, confidence, evidence_key "
                "FROM graph_edges WHERE from_id=? "
                "ORDER BY confidence DESC, edge_id ASC LIMIT ?"
            )
            params = (node_id, max_items)
        elif direction == "in":
            sql = (
                "SELECT edge_id, from_id, to_id, edge_type, confidence, evidence_key "
                "FROM graph_edges WHERE to_id=? "
                "ORDER BY confidence DESC, edge_id ASC LIMIT ?"
            )
            params = (node_id, max_items)
        else:
            sql = (
                "SELECT edge_id, from_id, to_id, edge_type, confidence, evidence_key "
                "FROM graph_edges WHERE from_id=? OR to_id=? "
                "ORDER BY confidence DESC, edge_id ASC LIMIT ?"
            )
            params = (node_id, node_id, max_items)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "edge_id": str(row["edge_id"]),
                "from_id": str(row["from_id"]),
                "to_id": str(row["to_id"]),
                "edge_type": str(row["edge_type"]),
                "confidence": float(row["confidence"]),
                "evidence_key": str(row["evidence_key"]),
            }
            for row in rows
        ]

    def subgraph(
        self,
        *,
        seed_node_id: str,
        depth: int = 1,
        max_edges: int = 1000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return a bounded subgraph using breadth-first expansion.

        Args:
            seed_node_id: The starting node ID for the subgraph.
            depth: The maximum depth to traverse from the seed node.
            max_edges: The maximum number of edges to include in the subgraph.

        Returns:
            A dictionary containing the nodes and edges of the subgraph.
        """

        depth = max(1, int(depth))
        max_edges = max(1, int(max_edges))

        seen_nodes: set[str] = {seed_node_id}
        seen_edges: dict[str, dict[str, Any]] = {}
        frontier: deque[tuple[str, int]] = deque([(seed_node_id, 0)])

        while frontier and len(seen_edges) < max_edges:
            current, current_depth = frontier.popleft()
            if current_depth >= depth:
                continue

            for edge in self.neighbours(node_id=current, direction="both", max_items=max_edges):
                edge_id = str(edge["edge_id"])
                if edge_id in seen_edges:
                    continue
                seen_edges[edge_id] = edge
                other = str(edge["to_id"] if edge["from_id"] == current else edge["from_id"])
                if other not in seen_nodes:
                    seen_nodes.add(other)
                    frontier.append((other, current_depth + 1))
                if len(seen_edges) >= max_edges:
                    break

        nodes: list[dict[str, Any]] = []
        for node_id in sorted(seen_nodes):
            try:
                nodes.append(self.get_node(node_id=node_id))
            except KeyError:
                continue

        edges = [seen_edges[edge_id] for edge_id in sorted(seen_edges.keys())]
        return {"nodes": nodes, "edges": edges}
