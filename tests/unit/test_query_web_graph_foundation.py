"""Unit tests for query_web/graph_foundation.py."""

from __future__ import annotations

import pytest

from query_web.graph_foundation import (
    GraphEdgeType,
    GraphNodeType,
    build_corpus_a_node_id,
    build_corpus_b_node_id,
    build_edge_dedupe_key,
)


def test_graph_node_and_edge_enums_have_expected_values() -> None:
    assert GraphNodeType.CORPUS_A_CONTROL == "CorpusAControl"
    assert GraphNodeType.CORPUS_B_GUIDANCE_CHUNK == "CorpusBGuidanceChunk"
    assert GraphEdgeType.BELONGS_TO_FRAMEWORK == "BELONGS_TO_FRAMEWORK"
    assert GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL == "GUIDANCE_SUPPORTS_CONTROL"


def test_build_corpus_a_node_id_is_stable_and_normalised() -> None:
    assert build_corpus_a_node_id("NIST-CSF-GV-GV-OC-01") == "a:nist-csf-gv-gv-oc-01"
    assert build_corpus_a_node_id(" nist-csf-gv-gv-oc-01 ") == "a:nist-csf-gv-gv-oc-01"


def test_build_corpus_a_node_id_requires_non_empty_value() -> None:
    with pytest.raises(ValueError, match="requirement_id"):
        build_corpus_a_node_id("   ")


def test_build_corpus_b_node_id_is_deterministic() -> None:
    node_id_1 = build_corpus_b_node_id(
        source_path="/a/path/doc.md",
        source_name="doc.md",
        normalised_text_sha256="abc123",
        content_sha256="",
        chunk_ordinal=2,
    )
    node_id_2 = build_corpus_b_node_id(
        source_path="/a/path/doc.md",
        source_name="doc.md",
        normalised_text_sha256="abc123",
        content_sha256="",
        chunk_ordinal=2,
    )
    assert node_id_1 == node_id_2
    assert node_id_1.startswith("b:")


def test_build_corpus_b_node_id_requires_identifying_metadata() -> None:
    with pytest.raises(ValueError, match="requires source/hash/ordinal"):
        build_corpus_b_node_id(
            source_path="",
            source_name="",
            normalised_text_sha256="",
            content_sha256="",
            chunk_ordinal=None,
        )


def test_build_edge_dedupe_key_is_stable() -> None:
    key_1 = build_edge_dedupe_key(
        from_id="a:nist-csf-gv-gv-oc-01",
        edge_type=GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        to_id="b:0123456789abcdef",
        evidence_key="snippet-001",
    )
    key_2 = build_edge_dedupe_key(
        from_id="A:NIST-CSF-GV-GV-OC-01",
        edge_type="guidance_supports_control",
        to_id="B:0123456789ABCDEF",
        evidence_key="SNIPPET-001",
    )
    assert key_1 == key_2
