"""Unit tests for query_web/graph_traversal.py."""

from __future__ import annotations

from dataclasses import dataclass

from query_web.graph_foundation import GraphEdgeType
from query_web.graph_traversal import (
    fanout_limit_for_depth,
    graph_size_band,
    guidance_threshold,
    marginal_gain_pct,
    resolve_requested_depth,
    select_edges_for_hop,
    should_stop_for_marginal_gain,
    traversal_depth_limits,
    traversal_truncation_metadata,
)


@dataclass
class _TraversalTestConfig:
    graph_size_small_edges: int = 50000
    graph_size_large_edges: int = 250000
    graph_depth_small_default: int = 3
    graph_depth_small_max: int = 4
    graph_depth_medium_default: int = 2
    graph_depth_medium_max: int = 3
    graph_depth_large_default: int = 1
    graph_depth_large_max: int = 2
    graph_fanout_depth1: int = 50
    graph_fanout_depth2: int = 20
    graph_fanout_depth3_plus: int = 8
    graph_guidance_threshold_small: float = 0.50
    graph_guidance_threshold_medium: float = 0.65
    graph_guidance_threshold_large: float = 0.75
    graph_min_marginal_new_node_gain_pct: float = 5.0
    graph_traversal_max_edges: int = 10000
    graph_traversal_max_payload_bytes: int = 2 * 1024 * 1024


def _config() -> _TraversalTestConfig:
    return _TraversalTestConfig()


def test_graph_size_band_and_depth_limits() -> None:
    cfg = _config()
    assert graph_size_band(edge_count=1000, config=cfg) == "small"
    assert graph_size_band(edge_count=100000, config=cfg) == "medium"
    assert graph_size_band(edge_count=300000, config=cfg) == "large"
    assert traversal_depth_limits(edge_count=1000, config=cfg) == (3, 4)
    assert traversal_depth_limits(edge_count=100000, config=cfg) == (2, 3)
    assert traversal_depth_limits(edge_count=300000, config=cfg) == (1, 2)


def test_resolve_requested_depth_is_clamped_to_band_max() -> None:
    cfg = _config()
    assert resolve_requested_depth(edge_count=1000, requested_depth=None, config=cfg) == 3
    assert resolve_requested_depth(edge_count=100000, requested_depth=9, config=cfg) == 3
    assert resolve_requested_depth(edge_count=300000, requested_depth=0, config=cfg) == 1


def test_fanout_and_guidance_threshold_by_size() -> None:
    cfg = _config()
    assert fanout_limit_for_depth(depth=1, config=cfg) == 50
    assert fanout_limit_for_depth(depth=2, config=cfg) == 20
    assert fanout_limit_for_depth(depth=3, config=cfg) == 8
    assert guidance_threshold(edge_count=1000, config=cfg) == 0.50
    assert guidance_threshold(edge_count=100000, config=cfg) == 0.65
    assert guidance_threshold(edge_count=300000, config=cfg) == 0.75


def test_marginal_gain_and_stop_rule() -> None:
    cfg = _config()
    assert marginal_gain_pct(existing_node_count=100, new_node_count=4) == 4.0
    assert should_stop_for_marginal_gain(existing_node_count=100, new_node_count=4, config=cfg)
    assert not should_stop_for_marginal_gain(
        existing_node_count=100,
        new_node_count=6,
        config=cfg,
    )


def test_select_edges_for_hop_keeps_structural_and_prunes_low_confidence_inferred() -> None:
    cfg = _config()
    edges = [
        {
            "edge_type": GraphEdgeType.BELONGS_TO_FRAMEWORK,
            "confidence": 0.01,
            "id": "struct-1",
        },
        {
            "edge_type": GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
            "confidence": 0.40,
            "id": "inferred-low",
        },
        {
            "edge_type": GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
            "confidence": 0.80,
            "id": "inferred-high",
        },
    ]
    selected = select_edges_for_hop(edges=edges, depth=1, edge_count=300000, config=cfg)
    selected_ids = {str(edge["id"]) for edge in selected if isinstance(edge, dict)}
    assert "struct-1" in selected_ids
    assert "inferred-high" in selected_ids
    assert "inferred-low" not in selected_ids


def test_truncation_metadata_flags_budget_exceeded() -> None:
    cfg = _config()
    meta = traversal_truncation_metadata(
        traversed_edges=10001,
        payload_bytes=(2 * 1024 * 1024) + 1,
        config=cfg,
    )
    assert meta["truncated"] is True
    assert "max_edges" in meta["truncation_reasons"]
    assert "max_payload_bytes" in meta["truncation_reasons"]
