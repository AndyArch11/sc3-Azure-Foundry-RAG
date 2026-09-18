"""Local batch graph artifact generation for Corpus A and Corpus B.

- Build deterministic node/edge artifacts from provided Corpus A controls and
  Corpus B guidance chunks.
- Emit local files: nodes.jsonl, edges.jsonl, graph_build_report.json.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from query_web.constants import GRAPH_SCHEMA_VERSION
from query_web.graph_artifacts import (
    GraphEdgeRecord,
    GraphNodeRecord,
    build_corpus_a_control_node,
    build_corpus_b_guidance_node,
    build_guidance_support_edge,
    build_structural_records_for_corpus_a_control,
    build_structural_records_for_corpus_b_guidance,
    dedupe_edge_records,
)
from query_web.graph_traversal import guidance_threshold


class _GraphThresholdConfig(Protocol):
    """Minimal config surface needed for guidance threshold selection.

    Attributes:
        graph_size_small_edges: The number of edges considered small.
        graph_size_large_edges: The number of edges considered large.
        graph_guidance_threshold_small: The guidance threshold for small graphs.
        graph_guidance_threshold_medium: The guidance threshold for medium graphs.
        graph_guidance_threshold_large: The guidance threshold for large graphs.
    """

    graph_size_small_edges: int
    graph_size_large_edges: int
    graph_guidance_threshold_small: float
    graph_guidance_threshold_medium: float
    graph_guidance_threshold_large: float


class _ArtifactBudgetConfig(_GraphThresholdConfig, Protocol):
    """Config needed by local artifact generation.

    Attributes:
        graph_traversal_max_edges: The maximum number of edges to traverse.
        graph_traversal_max_payload_bytes: The maximum payload size in bytes.
    """

    graph_traversal_max_edges: int
    graph_traversal_max_payload_bytes: int


def _now_iso_utc() -> str:
    """Return current time in UTC ISO format.

    Returns:
        The current time in UTC ISO format.
    """

    return datetime.now(UTC).isoformat()


def _is_corpus_b_chunk(chunk: dict[str, Any]) -> bool:
    """Return True when chunk metadata indicates Corpus B guidance.

    Args:
        chunk: The chunk metadata dictionary.

    Returns:
        True if the chunk is identified as Corpus B guidance, False otherwise.
    """

    corpus = str(chunk.get("corpus") or "").strip().lower().replace("_", "-")
    corpus_role = str(chunk.get("corpus_role") or "").strip().lower().replace("-", "_")
    return corpus in {"b", "corpus-b"} or corpus_role in {
        "narrative_guidance",
        "guidance",
        "narrative",
    }


def _tokenise(text: str) -> set[str]:
    """Tokenise text for simple lexical-overlap inference.

    Args:
        text: The text to tokenise.

    Returns:
        A set of tokens extracted from the text.
    """

    raw = str(text or "").lower()
    tokens = re.findall(r"[a-z0-9]{3,}", raw)
    return {token for token in tokens if token not in {"and", "the", "for", "with", "from"}}


def _guidance_support_confidence(
    control: GraphNodeRecord, guidance: GraphNodeRecord
) -> tuple[float, str]:
    """Compute lexical-overlap confidence and evidence key for support edge.

    Args:
        control: The control node record.
        guidance: The guidance node record.

    Returns:
        A tuple containing the confidence score and evidence key.
    """

    control_text = " ".join(
        [
            str(control.attributes.get("requirement_text") or ""),
            str(control.attributes.get("guidance_text") or ""),
            str(control.attributes.get("control_family") or ""),
        ]
    )
    guidance_text = " ".join(
        [
            str(guidance.attributes.get("content") or ""),
            str(guidance.attributes.get("source_name") or ""),
            str(guidance.attributes.get("source_path") or ""),
        ]
    )

    left = _tokenise(control_text)
    right = _tokenise(guidance_text)
    if not left or not right:
        return (0.0, "shared:")

    shared = sorted(left.intersection(right))
    overlap = len(shared)
    confidence = min(1.0, overlap / 8.0)
    evidence_key = "shared:" + ",".join(shared[:10])
    return (confidence, evidence_key)


def _dedupe_nodes(nodes: list[GraphNodeRecord]) -> list[GraphNodeRecord]:
    """Dedupe nodes by node_id with deterministic order.

    Args:
        nodes: A list of GraphNodeRecord instances.

    Returns:
        A list of deduplicated GraphNodeRecord instances.
    """

    by_id: dict[str, GraphNodeRecord] = {}
    for node in nodes:
        by_id.setdefault(node.node_id, node)
    return [by_id[node_id] for node_id in sorted(by_id.keys())]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write JSONL records with stable key ordering.

    Args:
        path: The path to the output JSONL file.
        records: A list of dictionaries to write as JSONL records.
    """

    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def build_local_graph_artifacts(
    *,
    controls: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    output_dir: str,
    config: _ArtifactBudgetConfig,
    min_community_size: int = 3,
) -> dict[str, Any]:
    """Build and persist local graph artifacts from Corpus A/B inputs.

    Args:
        controls: A list of control dictionaries.
        chunks: A list of chunk dictionaries.
        output_dir: The directory to write output files.
        config: The artifact budget configuration.

    Returns:
        A dictionary containing report metadata and emitted file paths.
    """

    min_community_size = max(2, int(min_community_size))

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes: list[GraphNodeRecord] = []
    edges: list[GraphEdgeRecord] = []
    dropped_controls = 0
    dropped_chunks = 0

    control_nodes: list[GraphNodeRecord] = []
    for control in controls:
        try:
            control_node = build_corpus_a_control_node(control)
        except ValueError:
            dropped_controls += 1
            continue
        control_nodes.append(control_node)
        nodes.append(control_node)
        extra_nodes, extra_edges = build_structural_records_for_corpus_a_control(control_node)
        nodes.extend(extra_nodes)
        edges.extend(extra_edges)

    guidance_nodes: list[GraphNodeRecord] = []
    chunk_ordinal = 0
    for chunk in chunks:
        if not _is_corpus_b_chunk(chunk):
            continue
        try:
            guidance_node = build_corpus_b_guidance_node(chunk, chunk_ordinal=chunk_ordinal)
        except ValueError:
            dropped_chunks += 1
            continue
        chunk_ordinal += 1
        guidance_nodes.append(guidance_node)
        nodes.append(guidance_node)
        extra_nodes, extra_edges = build_structural_records_for_corpus_b_guidance(guidance_node)
        nodes.extend(extra_nodes)
        edges.extend(extra_edges)

    threshold = guidance_threshold(edge_count=len(edges), config=config)
    inferred_count = 0
    for control_node in control_nodes:
        for guidance_node in guidance_nodes:
            confidence, evidence_key = _guidance_support_confidence(control_node, guidance_node)
            if confidence < threshold:
                continue
            inferred_count += 1
            edges.append(
                build_guidance_support_edge(
                    control_node=control_node,
                    guidance_node=guidance_node,
                    evidence_key=evidence_key,
                    confidence=confidence,
                )
            )

    deduped_nodes = _dedupe_nodes(nodes)
    deduped_edges = dedupe_edge_records(edges)

    node_dicts = [node.model_dump() for node in deduped_nodes]
    edge_dicts = [edge.model_dump() for edge in deduped_edges]
    nodes_path = out_dir / "nodes.jsonl"
    edges_path = out_dir / "edges.jsonl"
    _write_jsonl(nodes_path, node_dicts)
    _write_jsonl(edges_path, edge_dicts)

    report = {
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "built_at": _now_iso_utc(),
        "scope": {"include": ["a", "b"], "exclude": ["c"]},
        "input": {
            "controls_total": len(controls),
            "chunks_total": len(chunks),
            "corpus_b_chunks_total": len(guidance_nodes) + dropped_chunks,
        },
        "output": {
            "nodes_total": len(deduped_nodes),
            "edges_total": len(deduped_edges),
            "inferred_edges_total": inferred_count,
        },
        "dropped": {
            "controls_invalid": dropped_controls,
            "chunks_invalid": dropped_chunks,
        },
        "thresholds": {
            "guidance_support_threshold": threshold,
            "max_edges_budget": config.graph_traversal_max_edges,
            "max_payload_bytes_budget": config.graph_traversal_max_payload_bytes,
        },
        "community_settings": {
            "min_community_size": min_community_size,
        },
        "files": {
            "nodes_jsonl": str(nodes_path),
            "edges_jsonl": str(edges_path),
            "graph_build_report_json": str(out_dir / "graph_build_report.json"),
        },
    }

    report_path = out_dir / "graph_build_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return report
