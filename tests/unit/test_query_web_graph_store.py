"""Unit tests for query_web/graph_store.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from query_web.graph_batch import build_local_graph_artifacts
from query_web.graph_store import SqliteGraphStore


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        graph_size_small_edges=50000,
        graph_size_large_edges=250000,
        graph_guidance_threshold_small=0.25,
        graph_guidance_threshold_medium=0.65,
        graph_guidance_threshold_large=0.75,
        graph_traversal_max_edges=10000,
        graph_traversal_max_payload_bytes=2 * 1024 * 1024,
    )


def _controls() -> list[dict[str, object]]:
    return [
        {
            "requirement_id": "NIST-CSF-GV-GV-OC-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "GV.OC",
            "requirement_text": "Mission and stakeholders are understood.",
            "guidance_text": "Guidance for mission and stakeholders.",
            "source_uri": "https://example.com/nist-csf",
        }
    ]


def _chunks() -> list[dict[str, object]]:
    return [
        {
            "source_name": "guidance-1.md",
            "source_path": "/tmp/guidance-1.md",
            "normalised_text_sha256": "abc123",
            "content_sha256": "",
            "original_filename": "guidance-1.md",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Mission and stakeholder context for cybersecurity risk management.",
            "score": 0.8,
        }
    ]


def _build_artifacts(tmp_path: Path) -> dict[str, object]:
    return build_local_graph_artifacts(
        controls=_controls(),
        chunks=_chunks(),
        output_dir=str(tmp_path),
        config=_config(),
    )


def test_sqlite_graph_store_loads_artifacts_and_counts(tmp_path: Path) -> None:
    report = _build_artifacts(tmp_path)
    store = SqliteGraphStore(db_path=str(tmp_path / "graph.sqlite"))
    write_stats = store.load_artifact_files(
        nodes_jsonl=str(report["files"]["nodes_jsonl"]),
        edges_jsonl=str(report["files"]["edges_jsonl"]),
    )

    counts = store.counts()
    assert write_stats["nodes_written"] == counts["nodes"]
    assert write_stats["edges_written"] == counts["edges"]
    assert counts["nodes"] > 0
    assert counts["edges"] > 0


def test_sqlite_graph_store_get_node_raises_for_missing(tmp_path: Path) -> None:
    store = SqliteGraphStore(db_path=str(tmp_path / "graph.sqlite"))
    with pytest.raises(KeyError, match="Node not found"):
        store.get_node(node_id="missing-node")


def test_sqlite_graph_store_neighbors_and_subgraph(tmp_path: Path) -> None:
    report = _build_artifacts(tmp_path)
    store = SqliteGraphStore(db_path=str(tmp_path / "graph.sqlite"))
    store.load_artifact_files(
        nodes_jsonl=str(report["files"]["nodes_jsonl"]),
        edges_jsonl=str(report["files"]["edges_jsonl"]),
    )

    seed_node_id = "a:nist-csf-gv-gv-oc-01"
    neighbors = store.neighbours(node_id=seed_node_id, direction="both", max_items=20)
    assert neighbors

    subgraph = store.subgraph(seed_node_id=seed_node_id, depth=2, max_edges=100)
    assert subgraph["nodes"]
    assert subgraph["edges"]
    assert any(node.get("node_id") == seed_node_id for node in subgraph["nodes"])
