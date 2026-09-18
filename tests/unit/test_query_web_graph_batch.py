"""Unit tests for query_web/graph_batch.py."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from query_web.graph_batch import build_local_graph_artifacts


@dataclass
class _GraphBatchTestConfig:
    graph_size_small_edges: int = 50000
    graph_size_large_edges: int = 250000
    graph_guidance_threshold_small: float = 0.25
    graph_guidance_threshold_medium: float = 0.65
    graph_guidance_threshold_large: float = 0.75
    graph_traversal_max_edges: int = 10000
    graph_traversal_max_payload_bytes: int = 2 * 1024 * 1024


def _config() -> _GraphBatchTestConfig:
    return _GraphBatchTestConfig()


def _controls() -> list[dict[str, object]]:
    return [
        {
            "requirement_id": "NIST-CSF-GV-GV-OC-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "GV.OC",
            "requirement_text": "Organizational mission and stakeholder requirements are understood.",
            "guidance_text": "Mission and stakeholders context for cybersecurity risk management.",
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
            "content": "Mission and stakeholder context guidance for cybersecurity risk management.",
            "score": 0.8,
        },
        {
            "source_name": "artifact-c.md",
            "source_path": "/tmp/artifact-c.md",
            "normalised_text_sha256": "def456",
            "content_sha256": "",
            "original_filename": "artifact-c.md",
            "corpus": "c",
            "corpus_role": "assessed_artifact",
            "content": "This is corpus C and should be excluded.",
            "score": 0.9,
        },
    ]


def _read_lines(path: Path) -> list[str]:
    return [
        line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_build_local_graph_artifacts_writes_expected_files(tmp_path: Path) -> None:
    report = build_local_graph_artifacts(
        controls=_controls(),
        chunks=_chunks(),
        output_dir=str(tmp_path),
        config=_config(),
    )

    nodes_path = Path(report["files"]["nodes_jsonl"])
    edges_path = Path(report["files"]["edges_jsonl"])
    report_path = Path(report["files"]["graph_build_report_json"])

    assert nodes_path.exists()
    assert edges_path.exists()
    assert report_path.exists()

    report_json = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_json["scope"]["include"] == ["a", "b"]
    assert report_json["scope"]["exclude"] == ["c"]
    assert report_json["input"]["controls_total"] == 1
    assert report_json["input"]["chunks_total"] == 2


def test_build_local_graph_artifacts_is_deterministic_across_runs(tmp_path: Path) -> None:
    dir_one = tmp_path / "run-1"
    dir_two = tmp_path / "run-2"
    build_local_graph_artifacts(
        controls=_controls(),
        chunks=_chunks(),
        output_dir=str(dir_one),
        config=_config(),
    )
    build_local_graph_artifacts(
        controls=_controls(),
        chunks=_chunks(),
        output_dir=str(dir_two),
        config=_config(),
    )

    nodes_1 = _read_lines(dir_one / "nodes.jsonl")
    nodes_2 = _read_lines(dir_two / "nodes.jsonl")
    edges_1 = _read_lines(dir_one / "edges.jsonl")
    edges_2 = _read_lines(dir_two / "edges.jsonl")

    assert nodes_1 == nodes_2
    assert edges_1 == edges_2


def test_build_local_graph_artifacts_excludes_non_corpus_b_chunks(tmp_path: Path) -> None:
    report = build_local_graph_artifacts(
        controls=_controls(),
        chunks=_chunks(),
        output_dir=str(tmp_path),
        config=_config(),
    )
    report_json = json.loads(
        Path(report["files"]["graph_build_report_json"]).read_text(encoding="utf-8")
    )
    assert report_json["input"]["corpus_b_chunks_total"] == 1
