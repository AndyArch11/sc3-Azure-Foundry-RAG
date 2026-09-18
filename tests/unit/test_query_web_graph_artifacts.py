"""Unit tests for query_web/graph_artifacts.py."""

from __future__ import annotations

import pytest

from query_web.constants import GRAPH_SCHEMA_VERSION
from query_web.graph_artifacts import (
    GraphEdgeRecord,
    GraphNodeRecord,
    build_corpus_a_control_node,
    build_corpus_b_guidance_node,
    build_graph_edge_record,
    build_guidance_support_edge,
    build_structural_records_for_corpus_a_control,
    build_structural_records_for_corpus_b_guidance,
    dedupe_edge_records,
)
from query_web.graph_foundation import GraphEdgeType, GraphNodeType


def test_graph_node_record_uses_graph_schema_default() -> None:
    node = GraphNodeRecord(
        node_id="a:test-control",
        node_type=GraphNodeType.CORPUS_A_CONTROL,
        label="test-control",
    )
    assert node.graph_schema_version == GRAPH_SCHEMA_VERSION


def test_graph_edge_record_uses_graph_schema_default() -> None:
    edge = GraphEdgeRecord(
        edge_id="a|BELONGS_TO_FRAMEWORK|f|0",
        from_id="a:test-control",
        to_id="f:test-framework",
        edge_type=GraphEdgeType.BELONGS_TO_FRAMEWORK,
    )
    assert edge.graph_schema_version == GRAPH_SCHEMA_VERSION


def test_build_corpus_a_control_node_maps_required_fields() -> None:
    node = build_corpus_a_control_node(
        {
            "requirement_id": "NIST-CSF-GV-GV-OC-01",
            "framework": "NIST CSF",
            "framework_version": "2.0",
            "control_family": "GV.OC",
            "maturity_level": None,
            "requirement_text": "Mission is understood.",
            "guidance_text": "Guidance text",
            "source_uri": "https://example.com",
        }
    )
    assert node.node_id == "a:nist-csf-gv-gv-oc-01"
    assert node.node_type == GraphNodeType.CORPUS_A_CONTROL
    assert node.attributes["framework"] == "NIST CSF"


def test_build_corpus_a_control_node_requires_requirement_id() -> None:
    with pytest.raises(ValueError, match="requirement_id"):
        build_corpus_a_control_node({"framework": "NIST CSF"})


def test_build_corpus_b_guidance_node_maps_chunk_metadata() -> None:
    chunk = {
        "source_name": "guidance.md",
        "source_path": "/tmp/guidance.md",
        "normalised_text_sha256": "abc",
        "content_sha256": "",
        "original_filename": "guidance.md",
        "corpus": "b",
        "corpus_role": "narrative_guidance",
        "content": "Guidance content",
        "score": 0.9,
    }
    node = build_corpus_b_guidance_node(chunk, chunk_ordinal=3)
    assert node.node_id.startswith("b:")
    assert node.node_type == GraphNodeType.CORPUS_B_GUIDANCE_CHUNK
    assert node.attributes["corpus"] == "b"
    assert node.attributes["corpus_role"] == "narrative_guidance"


def test_build_graph_edge_record_produces_deterministic_edge_id() -> None:
    edge_1 = build_graph_edge_record(
        from_id="a:nist-csf-gv-gv-oc-01",
        to_id="b:123",
        edge_type=GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        evidence_key="snippet-1",
        confidence=0.7,
    )
    edge_2 = build_graph_edge_record(
        from_id="a:nist-csf-gv-gv-oc-01",
        to_id="b:123",
        edge_type=GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        evidence_key="snippet-1",
        confidence=0.2,
    )
    assert edge_1.edge_id == edge_2.edge_id
    assert edge_1.confidence == 0.7


def test_build_structural_records_for_corpus_a_control_returns_expected_edges() -> None:
    control_node = build_corpus_a_control_node(
        {
            "requirement_id": "NIST-CSF-GV-GV-OC-01",
            "framework": "NIST CSF",
            "control_family": "GV.OC",
            "source_uri": "https://example.com/control",
        }
    )

    nodes, edges = build_structural_records_for_corpus_a_control(control_node)

    assert len(nodes) == 3
    assert {edge.edge_type for edge in edges} == {
        GraphEdgeType.BELONGS_TO_FRAMEWORK,
        GraphEdgeType.IN_FAMILY,
        GraphEdgeType.DERIVED_FROM_SOURCE,
    }


def test_build_structural_records_for_corpus_b_guidance_returns_source_edge() -> None:
    guidance_node = build_corpus_b_guidance_node(
        {
            "source_name": "guidance.md",
            "source_path": "/tmp/guidance.md",
            "normalised_text_sha256": "abc",
            "content_sha256": "",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Guidance content",
        }
    )

    nodes, edges = build_structural_records_for_corpus_b_guidance(guidance_node)
    assert len(nodes) == 1
    assert len(edges) == 1
    assert edges[0].edge_type == GraphEdgeType.DERIVED_FROM_SOURCE


def test_build_guidance_support_edge_links_guidance_to_control() -> None:
    control_node = build_corpus_a_control_node(
        {
            "requirement_id": "NIST-CSF-GV-GV-OC-01",
            "framework": "NIST CSF",
        }
    )
    guidance_node = build_corpus_b_guidance_node(
        {
            "source_name": "guidance.md",
            "source_path": "/tmp/guidance.md",
            "normalised_text_sha256": "abc",
            "content_sha256": "",
            "corpus": "b",
            "corpus_role": "narrative_guidance",
            "content": "Guidance content",
        }
    )
    edge = build_guidance_support_edge(
        control_node=control_node,
        guidance_node=guidance_node,
        evidence_key="snippet-1",
        confidence=0.66,
    )
    assert edge.from_id == guidance_node.node_id
    assert edge.to_id == control_node.node_id
    assert edge.edge_type == GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL


def test_dedupe_edge_records_keeps_highest_confidence_and_is_stable() -> None:
    edge_low = build_graph_edge_record(
        from_id="b:1",
        to_id="a:1",
        edge_type=GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        evidence_key="snippet-1",
        confidence=0.2,
    )
    edge_high = build_graph_edge_record(
        from_id="b:1",
        to_id="a:1",
        edge_type=GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        evidence_key="snippet-1",
        confidence=0.9,
    )
    other = build_graph_edge_record(
        from_id="a:1",
        to_id="framework:1",
        edge_type=GraphEdgeType.BELONGS_TO_FRAMEWORK,
    )
    result = dedupe_edge_records([edge_low, other, edge_high])
    assert len(result) == 2
    assert any(e.edge_id == edge_high.edge_id and e.confidence == 0.9 for e in result)
