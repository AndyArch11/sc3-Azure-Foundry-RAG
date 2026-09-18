"""Graph backend interface and factory for local-first adapter readiness."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from query_web.graph_artifacts import GraphEdgeRecord, GraphNodeRecord
from query_web.graph_store import SqliteGraphStore


class GraphStoreBackend(Protocol):
    """Minimal backend contract used by graph endpoints.

    This protocol defines the essential methods that any graph store backend
    must implement to be compatible with the graph endpoints.

    Attributes:
        None
    """

    def load_artifact_files(self, *, nodes_jsonl: str, edges_jsonl: str) -> dict[str, int]:
        """Load graph artifact files into the backend.

        Args:
            nodes_jsonl: Path to the nodes JSONL file.
            edges_jsonl: Path to the edges JSONL file.

        Returns:
            A dictionary with counts of loaded nodes and edges.
        """
        ...

    def counts(self) -> dict[str, int]:
        """Get counts of nodes and edges in the backend.

        Returns:
            A dictionary with counts of nodes and edges.
        """
        ...

    def get_node(self, *, node_id: str) -> dict[str, Any]:
        """Get a node by its ID.

        Args:
            node_id: The ID of the node to retrieve.

        Returns:
            A dictionary representing the node.
        """
        ...

    def subgraph(
        self,
        *,
        seed_node_id: str,
        depth: int = 1,
        max_edges: int = 1000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get a subgraph starting from a seed node.

        Args:
            seed_node_id: The ID of the seed node.
            depth: The depth of the subgraph.
            max_edges: The maximum number of edges to include.

        Returns:
            A dictionary representing the subgraph.
        """
        ...

    def export_graph(self, *, max_nodes: int = 100000, max_edges: int = 100000) -> dict[str, Any]:
        """Export the entire graph with optional limits.

        Args:
            max_nodes: The maximum number of nodes to include.
            max_edges: The maximum number of edges to include.

        Returns:
            A dictionary representing the exported graph.
        """
        ...


class GraphSnapshotSource(Protocol):
    """Source of graph snapshot artifacts for Azure adapter reads.

    This protocol defines the methods that any graph snapshot source must implement
    to be compatible with the Azure graph adapter.

    Attributes:
        None
    """

    def artifact_state(self) -> tuple[int, int, int, int] | None:
        """Get the state of the graph artifacts.

        Returns:
            A tuple containing the modification time and size of the nodes and edges artifacts,
            or None if the artifacts are not available.
        """
        ...

    def read_snapshot(self) -> tuple[str, str] | None:
        """Read the graph snapshot artifacts.

        Returns:
            A tuple containing the contents of the nodes and edges artifacts,
            or None if the artifacts are not available.
        """
        ...


class FileSystemGraphSnapshotSource:
    """Read graph snapshot artifacts from a local directory.
    Initial implementation is with a local-emulation mode so endpoint/back-end plumbing can
    be exercised before Azure-native graph storage is wired.

    Attributes:
        _artifacts_dir: The directory containing the graph snapshot artifacts.
    """

    def __init__(self, artifacts_dir: str) -> None:
        """Initialise the FileSystemGraphSnapshotSource with the given artifacts directory.

        Args:
            artifacts_dir: The directory containing the graph snapshot artifacts.
        """
        self._artifacts_dir = artifacts_dir.strip()

    def _artifact_paths(self) -> tuple[Path, Path] | None:
        """Return the paths to the nodes and edges artifacts if they exist.

        Returns:
            A tuple containing the paths to the nodes and edges artifacts, or None if they do not exist.
        """
        if not self._artifacts_dir:
            return None
        root = Path(self._artifacts_dir)
        nodes_path = root / "nodes.jsonl"
        edges_path = root / "edges.jsonl"
        if not nodes_path.exists() or not edges_path.exists():
            return None
        return nodes_path, edges_path

    def artifact_state(self) -> tuple[int, int, int, int] | None:
        """Return the modification time and size of the graph artifacts.

        Returns:
            A tuple containing the modification time and size of the nodes and edges artifacts,
            or None if the artifacts are not available.
        """
        paths = self._artifact_paths()
        if paths is None:
            return None
        nodes_path, edges_path = paths
        nodes_stat = nodes_path.stat()
        edges_stat = edges_path.stat()
        return (
            int(nodes_stat.st_mtime_ns),
            int(nodes_stat.st_size),
            int(edges_stat.st_mtime_ns),
            int(edges_stat.st_size),
        )

    def read_snapshot(self) -> tuple[str, str] | None:
        """Read the graph snapshot artifacts.

        Returns:
            A tuple containing the contents of the nodes and edges artifacts,
            or None if the artifacts are not available.
        """
        paths = self._artifact_paths()
        if paths is None:
            return None
        nodes_path, edges_path = paths
        return (
            nodes_path.read_text(encoding="utf-8"),
            edges_path.read_text(encoding="utf-8"),
        )


class ObjectStorageGraphSnapshotSource:
    """Read graph snapshot artifacts from an object/container storage client.

    Attributes:
        _client: The object storage client used to access the artifacts.
        _bucket_or_container: The name of the bucket or container containing the artifacts.
        _prefix: An optional prefix for the artifact keys in the storage.
    """

    def __init__(self, *, client: Any, bucket_or_container: str, prefix: str = "") -> None:
        """Initialise the ObjectStorageGraphSnapshotSource with the given client and storage details.

        Args:
            client: The object storage client used to access the artifacts.
            bucket_or_container: The name of the bucket or container containing the artifacts.
            prefix: An optional prefix for the artifact keys in the storage.
        """
        self._client = client
        self._bucket_or_container = bucket_or_container.strip()
        self._prefix = prefix.strip().strip("/")

    def _key(self, name: str) -> str:
        """Return the full key for an artifact in the object storage.

        Args:
            name: The name of the artifact (e.g., "nodes.jsonl", "edges.jsonl").

        Returns:
            The full key for the artifact in the object storage, including the prefix if set.
        """
        return f"{self._prefix}/{name}" if self._prefix else name

    def _metadata(self, key: str) -> dict[str, Any] | None:
        """Return the metadata for an artifact in the object storage.

        Args:
            key: The key of the artifact in the object storage.
        Returns:
            A dictionary containing the metadata of the artifact, or None if the artifact does not exist.
        """
        if not self._bucket_or_container:
            return None
        try:
            raw = self._client.get_object_metadata(self._bucket_or_container, key)
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def artifact_state(self) -> tuple[int, int, int, int] | None:
        """Return the state of the artifacts in the object storage.

        Returns:
            A tuple containing the last modified timestamps and content lengths of the nodes and edges artifacts,
            or None if any of the artifacts do not exist.
        """
        nodes_meta = self._metadata(self._key("nodes.jsonl"))
        edges_meta = self._metadata(self._key("edges.jsonl"))
        if nodes_meta is None or edges_meta is None:
            return None

        def _stamp(meta: dict[str, Any]) -> int:
            value = meta.get("last_modified")
            if isinstance(value, str) and value:
                try:
                    return int(
                        datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                        * 1_000_000_000
                    )
                except ValueError:
                    return 0
            return 0

        return (
            _stamp(nodes_meta),
            int(meta_val if isinstance((meta_val := nodes_meta.get("content_length")), int) else 0),
            _stamp(edges_meta),
            int(meta_val if isinstance((meta_val := edges_meta.get("content_length")), int) else 0),
        )

    def read_snapshot(self) -> tuple[str, str] | None:
        """Read the snapshot of the artifacts from the object storage.

        Returns:
            A tuple containing the contents of the nodes and edges artifacts as strings,
            or None if any of the artifacts do not exist.
        """
        if not self._bucket_or_container:
            return None
        try:
            nodes_bytes = self._client.get_object(
                self._bucket_or_container, self._key("nodes.jsonl")
            )
            edges_bytes = self._client.get_object(
                self._bucket_or_container, self._key("edges.jsonl")
            )
        except Exception:
            return None
        return (nodes_bytes.decode("utf-8"), edges_bytes.decode("utf-8"))


class AzureGraphStoreAdapter:
    """Azure backend scaffold.

    Milestone 3 starts with a local-emulation mode so endpoint/back-end plumbing can
    be exercised before Azure-native graph storage is wired.

    Attributes:
        _local_store: An optional local graph store backend for emulation.
        _artifacts_dir: The directory containing the graph snapshot artifacts for local emulation.
        _snapshot_source: An optional snapshot source for reading graph artifacts from Azure.
        _last_artifact_state: The last known state of the graph artifacts.
        _artifact_nodes: A dictionary of artifact nodes keyed by node_id.
        _artifact_edges: A dictionary of artifact edges keyed by edge_id.
    """

    def __init__(
        self,
        *,
        local_store: GraphStoreBackend | None = None,
        artifacts_dir: str = "",
        snapshot_source: GraphSnapshotSource | None = None,
    ) -> None:
        """Initialise the AzureGraphStoreAdapter with optional local store and snapshot source.

        Args:
            local_store: An optional local graph store backend for emulation.
            artifacts_dir: The directory containing the graph snapshot artifacts for local emulation.
            snapshot_source: An optional snapshot source for reading graph artifacts from Azure.
        """
        self._local_store = local_store
        self._artifacts_dir = artifacts_dir.strip()
        self._snapshot_source = snapshot_source
        if self._snapshot_source is None and self._artifacts_dir:
            self._snapshot_source = FileSystemGraphSnapshotSource(self._artifacts_dir)
        self._last_artifact_state: tuple[int, int, int, int] | None = None
        self._artifact_nodes: dict[str, dict[str, Any]] = {}
        self._artifact_edges: dict[str, dict[str, Any]] = {}

    def _uses_artifact_snapshot(self) -> bool:
        """Determine if the adapter is using artifact snapshot mode.

        Returns:
            True if the adapter is using artifact snapshot mode, False otherwise.
        """
        return self._snapshot_source is not None

    def _load_snapshot_records(self, *, nodes_text: str, edges_text: str) -> dict[str, int]:
        """Load nodes and edges from snapshot text into internal dictionaries.

        Args:
            nodes_text: The text content of the nodes JSONL artifact.
            edges_text: The text content of the edges JSONL artifact.

        Returns:
            A dictionary with counts of loaded nodes and edges.
        """
        nodes: dict[str, dict[str, Any]] = {}
        for line in nodes_text.splitlines():
            text = line.strip()
            if not text:
                continue
            record = GraphNodeRecord.model_validate_json(text)
            nodes[record.node_id] = record.model_dump(mode="json")

        edges: dict[str, dict[str, Any]] = {}
        for line in edges_text.splitlines():
            text = line.strip()
            if not text:
                continue
            edge_record = GraphEdgeRecord.model_validate_json(text)
            edges[edge_record.edge_id] = edge_record.model_dump(mode="json")

        self._artifact_nodes = nodes
        self._artifact_edges = edges
        return {"nodes_written": len(nodes), "edges_written": len(edges)}

    def _neighbours_from_snapshot(self, *, node_id: str, max_items: int) -> list[dict[str, Any]]:
        """Return edges connected to a node from the snapshot, sorted by confidence and edge_id.

        Args:
            node_id: The ID of the node for which to find connected edges.
            max_items: The maximum number of edges to return.

        Returns:
            A list of dictionaries representing the connected edges, sorted by confidence and edge_id.
        """
        edges = [
            edge
            for edge in self._artifact_edges.values()
            if edge.get("from_id") == node_id or edge.get("to_id") == node_id
        ]
        edges.sort(
            key=lambda edge: (
                -float(edge.get("confidence", 0.0)),
                str(edge.get("edge_id", "")),
            )
        )
        return [
            {
                "edge_id": str(edge.get("edge_id", "")),
                "from_id": str(edge.get("from_id", "")),
                "to_id": str(edge.get("to_id", "")),
                "edge_type": str(edge.get("edge_type", "")),
                "confidence": float(edge.get("confidence", 0.0)),
                "evidence_key": str(edge.get("evidence_key", "")),
            }
            for edge in edges[:max_items]
        ]

    def _refresh_from_artifacts_if_needed(self) -> None:
        """Refresh internal state from artifact snapshot if the state has changed.

        This method checks the current state of the artifact snapshot and reloads the nodes and edges if the state has changed since the last load.
        """
        snapshot_source = self._snapshot_source
        if snapshot_source is None:
            return
        current_state = snapshot_source.artifact_state()
        if current_state is None:
            return
        if self._last_artifact_state == current_state:
            return
        snapshot = snapshot_source.read_snapshot()
        if snapshot is None:
            return
        nodes_text, edges_text = snapshot
        self._load_snapshot_records(nodes_text=nodes_text, edges_text=edges_text)
        self._last_artifact_state = current_state

    def load_artifact_files(self, *, nodes_jsonl: str, edges_jsonl: str) -> dict[str, int]:
        """Load artifact files into the graph backend.

        Args:
            nodes_jsonl: Path to the nodes JSONL file.
            edges_jsonl: Path to the edges JSONL file.

        Returns:
            A dictionary with counts of loaded nodes and edges.
        """
        if self._uses_artifact_snapshot():
            result = self._load_snapshot_records(
                nodes_text=Path(nodes_jsonl).read_text(encoding="utf-8"),
                edges_text=Path(edges_jsonl).read_text(encoding="utf-8"),
            )
        else:
            if self._local_store is None:
                raise RuntimeError("Azure graph adapter has no backing store configured.")
            result = self._local_store.load_artifact_files(
                nodes_jsonl=nodes_jsonl, edges_jsonl=edges_jsonl
            )
        self._last_artifact_state = None
        return result

    def counts(self) -> dict[str, int]:
        """Return counts of nodes and edges in the graph backend.

        Returns:
            A dictionary with counts of nodes and edges.
        """
        if self._uses_artifact_snapshot():
            self._refresh_from_artifacts_if_needed()
            return {"nodes": len(self._artifact_nodes), "edges": len(self._artifact_edges)}
        if self._local_store is None:
            raise RuntimeError("Azure graph adapter has no backing store configured.")
        return self._local_store.counts()

    def get_node(self, *, node_id: str) -> dict[str, Any]:
        """Return a node by its ID from the graph backend.

        Args:
            node_id: The ID of the node to retrieve.
        Returns:
            A dictionary representing the node.
        Raises:
            KeyError: If the node is not found.
        """
        if self._uses_artifact_snapshot():
            self._refresh_from_artifacts_if_needed()
            node = self._artifact_nodes.get(node_id)
            if node is None:
                raise KeyError(f"Node not found: {node_id!r}")
            return dict(node)
        if self._local_store is None:
            raise RuntimeError("Azure graph adapter has no backing store configured.")
        return self._local_store.get_node(node_id=node_id)

    def subgraph(
        self,
        *,
        seed_node_id: str,
        depth: int = 1,
        max_edges: int = 1000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return a subgraph starting from a seed node.

        Args:
            seed_node_id: The ID of the seed node.
            depth: The maximum depth to traverse from the seed node.
            max_edges: The maximum number of edges to include in the subgraph.

        Returns:
            A dictionary with lists of nodes and edges in the subgraph.
        """
        if self._uses_artifact_snapshot():
            self._refresh_from_artifacts_if_needed()
            depth = max(1, int(depth))
            max_edges = max(1, int(max_edges))

            seen_nodes: set[str] = {seed_node_id}
            seen_edges: dict[str, dict[str, Any]] = {}
            frontier: list[tuple[str, int]] = [(seed_node_id, 0)]

            while frontier and len(seen_edges) < max_edges:
                current, current_depth = frontier.pop(0)
                if current_depth >= depth:
                    continue

                for edge in self._neighbours_from_snapshot(node_id=current, max_items=max_edges):
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

            nodes = [
                dict(self._artifact_nodes[node_id])
                for node_id in sorted(seen_nodes)
                if node_id in self._artifact_nodes
            ]
            edges = [seen_edges[edge_id] for edge_id in sorted(seen_edges.keys())]
            return {"nodes": nodes, "edges": edges}
        if self._local_store is None:
            raise RuntimeError("Azure graph adapter has no backing store configured.")
        return self._local_store.subgraph(
            seed_node_id=seed_node_id, depth=depth, max_edges=max_edges
        )

    def export_graph(self, *, max_nodes: int = 100000, max_edges: int = 100000) -> dict[str, Any]:
        """Export the entire graph with optional limits.

        Args:
            max_nodes: The maximum number of nodes to include in the export.
            max_edges: The maximum number of edges to include in the export.
        Returns:
            A dictionary representing the exported graph.
        """
        if self._uses_artifact_snapshot():
            self._refresh_from_artifacts_if_needed()
            nodes = [
                dict(self._artifact_nodes[node_id])
                for node_id in sorted(self._artifact_nodes.keys())[: max(1, int(max_nodes))]
            ]
            edges = [
                dict(self._artifact_edges[edge_id])
                for edge_id in sorted(self._artifact_edges.keys())[: max(1, int(max_edges))]
            ]
            return {"nodes": nodes, "edges": edges}
        if self._local_store is None:
            raise RuntimeError("Azure graph adapter has no backing store configured.")
        return self._local_store.export_graph(max_nodes=max_nodes, max_edges=max_edges)


class AwsGraphStoreAdapter(AzureGraphStoreAdapter):
    """AWS backend scaffold using the same snapshot-backed semantics as Azure rollout.

    Attributes:
        Inherits from AzureGraphStoreAdapter.
    """


def resolve_graph_backend(config: Any) -> str:
    """Return configured graph backend name with local fallback.

    Args:
        config: The configuration object.

    Returns:
        The name of the configured graph backend.
    """

    backend = str(getattr(config, "graph_backend", "local") or "local").strip().lower()
    return backend or "local"


_GRAPH_BACKEND_FACTORIES: dict[str, Callable[[Any], GraphStoreBackend]] = {
    "local": lambda config: SqliteGraphStore(),
}


def register_graph_backend(name: str, factory: Callable[[Any], GraphStoreBackend]) -> None:
    """Register or replace a graph backend factory.

    This supports staged multiple platform wiring (local -> azure -> aws) without changing
    endpoint logic.

    Args:
        name: The name of the graph backend (e.g., "local", "azure", "aws").
        factory: A callable that takes a config object and returns a GraphStoreBackend instance.
    Raises:
        ValueError: If the backend name is empty.
    """

    backend = str(name or "").strip().lower()
    if not backend:
        raise ValueError("Backend name must not be empty.")
    _GRAPH_BACKEND_FACTORIES[backend] = factory


def create_graph_store(config: Any) -> GraphStoreBackend:
    """Instantiate the configured graph backend.

    Local is the only implemented backend in Milestone 2. Azure/AWS intentionally
    return a clear not-implemented error for readiness planning and rollout gates.

    Args:
        config: The configuration object.
    Returns:
        An instance of GraphStoreBackend corresponding to the configured backend.
    Raises:
        NotImplementedError: If the configured backend is not yet implemented.
        ValueError: If the configured backend is unsupported.
    """

    backend = resolve_graph_backend(config)
    if backend in _GRAPH_BACKEND_FACTORIES:
        return _GRAPH_BACKEND_FACTORIES[backend](config)
    if backend == "azure":
        emulation_enabled = bool(getattr(config, "graph_azure_emulation_enabled", False))
        artifacts_dir = str(getattr(config, "graph_azure_artifacts_dir", "") or "").strip()
        artifacts_container = str(
            getattr(config, "graph_azure_artifacts_container", "") or ""
        ).strip()
        artifacts_prefix = str(getattr(config, "graph_azure_artifacts_prefix", "") or "").strip()
        storage_client = getattr(config, "graph_azure_storage_client", None)
        if emulation_enabled or artifacts_dir:
            return AzureGraphStoreAdapter(
                local_store=None if artifacts_dir else SqliteGraphStore(),
                artifacts_dir=artifacts_dir,
            )
        if storage_client is not None and artifacts_container:
            return AzureGraphStoreAdapter(
                snapshot_source=ObjectStorageGraphSnapshotSource(
                    client=storage_client,
                    bucket_or_container=artifacts_container,
                    prefix=artifacts_prefix,
                )
            )
        raise NotImplementedError(
            "Graph backend 'azure' is not available yet. "
            "Set GRAPH_AZURE_EMULATION_ENABLED=true or provide GRAPH_AZURE_ARTIFACTS_DIR "
            "for local parity mode, or use GRAPH_BACKEND=local."
        )
    if backend == "aws":
        artifacts_bucket = str(
            getattr(config, "graph_aws_artifacts_bucket", "")
            or getattr(config, "s3_bucket_name", "")
            or ""
        ).strip()
        artifacts_prefix = str(getattr(config, "graph_aws_artifacts_prefix", "") or "").strip()
        storage_client = getattr(config, "graph_aws_storage_client", None)
        if storage_client is not None and artifacts_bucket:
            return AwsGraphStoreAdapter(
                snapshot_source=ObjectStorageGraphSnapshotSource(
                    client=storage_client,
                    bucket_or_container=artifacts_bucket,
                    prefix=artifacts_prefix,
                )
            )
        raise NotImplementedError(
            "Graph backend 'aws' is not available yet. "
            "Provide GRAPH_AWS_ARTIFACTS_BUCKET plus a runtime graph_aws_storage_client, or use GRAPH_BACKEND=local."
        )
    raise ValueError(f"Unsupported GRAPH_BACKEND '{backend}'. Allowed values: local, azure, aws.")
