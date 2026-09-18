from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from query_web.graph_backend import (
    AwsGraphStoreAdapter,
    AzureGraphStoreAdapter,
    create_graph_store,
    register_graph_backend,
    resolve_graph_backend,
)
from query_web.graph_foundation import GraphNodeType
from query_web.graph_store import SqliteGraphStore


class _DummyStore:
    def load_artifact_files(self, *, nodes_jsonl: str, edges_jsonl: str) -> dict[str, int]:
        return {"nodes_written": 0, "edges_written": 0}

    def counts(self) -> dict[str, int]:
        return {"nodes": 0, "edges": 0}

    def get_node(self, *, node_id: str) -> dict[str, object]:
        return {"node_id": node_id}

    def subgraph(
        self,
        *,
        seed_node_id: str,
        depth: int = 1,
        max_edges: int = 1000,
    ) -> dict[str, list[dict[str, object]]]:
        return {"nodes": [], "edges": []}

    def export_graph(
        self, *, max_nodes: int = 100000, max_edges: int = 100000
    ) -> dict[str, object]:
        return {"nodes": [], "edges": []}


class _DummyObjectStorageClient:
    def __init__(
        self, *, objects: dict[str, bytes], metadata: dict[str, dict[str, object]]
    ) -> None:
        self._objects = objects
        self._metadata = metadata

    def get_object_metadata(self, bucket_or_container: str, key: str) -> dict[str, object]:
        return dict(self._metadata[f"{bucket_or_container}/{key}"])

    def get_object(self, bucket_or_container: str, key: str) -> bytes:
        return self._objects[f"{bucket_or_container}/{key}"]


def test_resolve_graph_backend_defaults_to_local_when_missing() -> None:
    cfg = SimpleNamespace()
    assert resolve_graph_backend(cfg) == "local"


def test_resolve_graph_backend_normalises_value() -> None:
    cfg = SimpleNamespace(graph_backend=" AZURE ")
    assert resolve_graph_backend(cfg) == "azure"


def test_create_graph_store_returns_sqlite_for_local() -> None:
    cfg = SimpleNamespace(graph_backend="local")
    store = create_graph_store(cfg)
    assert isinstance(store, SqliteGraphStore)


def test_create_graph_store_raises_for_unimplemented_backends() -> None:
    with pytest.raises(NotImplementedError):
        create_graph_store(SimpleNamespace(graph_backend="azure"))
    with pytest.raises(NotImplementedError):
        create_graph_store(SimpleNamespace(graph_backend="aws"))


def test_create_graph_store_azure_emulation_returns_adapter() -> None:
    store = create_graph_store(
        SimpleNamespace(graph_backend="azure", graph_azure_emulation_enabled=True)
    )
    assert isinstance(store, AzureGraphStoreAdapter)


def test_create_graph_store_azure_artifacts_dir_returns_adapter(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    store = create_graph_store(
        SimpleNamespace(
            graph_backend="azure",
            graph_azure_emulation_enabled=False,
            graph_azure_artifacts_dir=str(artifacts_dir),
        )
    )
    assert isinstance(store, AzureGraphStoreAdapter)


def test_azure_adapter_refreshes_from_artifact_snapshot(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "azure-graph.sqlite"
    monkeypatch.setenv("LOCAL_STATE_DB_PATH", str(db_path))

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = artifacts_dir / "nodes.jsonl"
    edges_path = artifacts_dir / "edges.jsonl"

    node = {
        "node_id": "a:nist-csf-gv-gv-oc-01",
        "node_type": GraphNodeType.CORPUS_A_CONTROL,
        "label": "NIST-CSF-GV-GV-OC-01",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    edge = {
        "edge_id": "e:1",
        "from_id": "a:nist-csf-gv-gv-oc-01",
        "to_id": "b:doc#chunk",
        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
        "confidence": 0.8,
        "evidence_key": "ev:1",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    nodes_path.write_text(json.dumps(node) + "\n", encoding="utf-8")
    edges_path.write_text(json.dumps(edge) + "\n", encoding="utf-8")

    store = create_graph_store(
        SimpleNamespace(
            graph_backend="azure",
            graph_azure_emulation_enabled=False,
            graph_azure_artifacts_dir=str(artifacts_dir),
        )
    )

    counts = store.counts()
    assert counts["nodes"] == 1
    assert counts["edges"] == 1

    loaded = store.get_node(node_id="a:nist-csf-gv-gv-oc-01")
    assert loaded["node_id"] == "a:nist-csf-gv-gv-oc-01"
    assert not db_path.exists()


def test_create_graph_store_azure_object_storage_snapshot_returns_adapter() -> None:
    client = _DummyObjectStorageClient(objects={}, metadata={})
    store = create_graph_store(
        SimpleNamespace(
            graph_backend="azure",
            graph_azure_storage_client=client,
            graph_azure_artifacts_container="graph-artifacts",
            graph_azure_artifacts_prefix="env/dev/graph",
        )
    )
    assert isinstance(store, AzureGraphStoreAdapter)


def test_create_graph_store_aws_object_storage_snapshot_returns_adapter() -> None:
    client = _DummyObjectStorageClient(objects={}, metadata={})
    store = create_graph_store(
        SimpleNamespace(
            graph_backend="aws",
            graph_aws_storage_client=client,
            graph_aws_artifacts_bucket="graph-artifacts-aws",
            graph_aws_artifacts_prefix="env/dev/graph",
        )
    )
    assert isinstance(store, AwsGraphStoreAdapter)


def test_azure_adapter_reads_snapshot_from_object_storage() -> None:
    node = {
        "node_id": "a:nist-csf-gv-gv-oc-01",
        "node_type": GraphNodeType.CORPUS_A_CONTROL,
        "label": "NIST-CSF-GV-GV-OC-01",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    edge = {
        "edge_id": "e:1",
        "from_id": "a:nist-csf-gv-gv-oc-01",
        "to_id": "b:doc#chunk",
        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
        "confidence": 0.8,
        "evidence_key": "ev:1",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    client = _DummyObjectStorageClient(
        objects={
            "graph-artifacts/env/dev/graph/nodes.jsonl": (json.dumps(node) + "\n").encode("utf-8"),
            "graph-artifacts/env/dev/graph/edges.jsonl": (json.dumps(edge) + "\n").encode("utf-8"),
        },
        metadata={
            "graph-artifacts/env/dev/graph/nodes.jsonl": {
                "content_length": len(json.dumps(node) + "\n"),
                "last_modified": "2026-07-06T12:00:00+00:00",
            },
            "graph-artifacts/env/dev/graph/edges.jsonl": {
                "content_length": len(json.dumps(edge) + "\n"),
                "last_modified": "2026-07-06T12:00:01+00:00",
            },
        },
    )

    store = create_graph_store(
        SimpleNamespace(
            graph_backend="azure",
            graph_azure_storage_client=client,
            graph_azure_artifacts_container="graph-artifacts",
            graph_azure_artifacts_prefix="env/dev/graph",
        )
    )

    counts = store.counts()
    assert counts == {"nodes": 1, "edges": 1}
    loaded = store.get_node(node_id="a:nist-csf-gv-gv-oc-01")
    assert loaded["node_id"] == "a:nist-csf-gv-gv-oc-01"


def test_aws_adapter_reads_snapshot_from_object_storage() -> None:
    node = {
        "node_id": "a:nist-csf-gv-gv-oc-01",
        "node_type": GraphNodeType.CORPUS_A_CONTROL,
        "label": "NIST-CSF-GV-GV-OC-01",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    edge = {
        "edge_id": "e:1",
        "from_id": "a:nist-csf-gv-gv-oc-01",
        "to_id": "b:doc#chunk",
        "edge_type": "GUIDANCE_SUPPORTS_CONTROL",
        "confidence": 0.8,
        "evidence_key": "ev:1",
        "graph_schema_version": "v1",
        "attributes": {},
    }
    client = _DummyObjectStorageClient(
        objects={
            "graph-artifacts-aws/env/dev/graph/nodes.jsonl": (json.dumps(node) + "\n").encode(
                "utf-8"
            ),
            "graph-artifacts-aws/env/dev/graph/edges.jsonl": (json.dumps(edge) + "\n").encode(
                "utf-8"
            ),
        },
        metadata={
            "graph-artifacts-aws/env/dev/graph/nodes.jsonl": {
                "content_length": len(json.dumps(node) + "\n"),
                "last_modified": "2026-07-06T12:00:00+00:00",
            },
            "graph-artifacts-aws/env/dev/graph/edges.jsonl": {
                "content_length": len(json.dumps(edge) + "\n"),
                "last_modified": "2026-07-06T12:00:01+00:00",
            },
        },
    )

    store = create_graph_store(
        SimpleNamespace(
            graph_backend="aws",
            graph_aws_storage_client=client,
            graph_aws_artifacts_bucket="graph-artifacts-aws",
            graph_aws_artifacts_prefix="env/dev/graph",
        )
    )

    counts = store.counts()
    assert counts == {"nodes": 1, "edges": 1}
    loaded = store.get_node(node_id="a:nist-csf-gv-gv-oc-01")
    assert loaded["node_id"] == "a:nist-csf-gv-gv-oc-01"


def test_create_graph_store_raises_for_unknown_backend() -> None:
    with pytest.raises(ValueError):
        create_graph_store(SimpleNamespace(graph_backend="neo4j"))


def test_register_graph_backend_allows_custom_factory() -> None:
    register_graph_backend("test", lambda config: _DummyStore())
    store = create_graph_store(SimpleNamespace(graph_backend="test"))
    assert isinstance(store, _DummyStore)
