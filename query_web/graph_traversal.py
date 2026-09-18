"""Size-aware traversal heuristics for relationship-graph queries.

These helpers are backend-agnostic and rely only on QueryConfig-style fields,
so they can be reused across local, Azure, and AWS graph backends.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from query_web.graph_foundation import STRUCTURAL_EDGE_TYPES, GraphEdgeType

GraphSizeBand = Literal["small", "medium", "large"]


class _GraphSizeBandConfig(Protocol):
    """Minimal config required to classify graph size bands.

    Attributes:
        graph_size_small_edges: The number of edges considered small.
        graph_size_large_edges: The number of edges considered large.
    """

    graph_size_small_edges: int
    graph_size_large_edges: int


class _GraphGuidanceThresholdConfig(_GraphSizeBandConfig, Protocol):
    """Minimal config required to resolve guidance confidence thresholds.

    Attributes:
        graph_guidance_threshold_small: The confidence threshold for small graphs.
        graph_guidance_threshold_medium: The confidence threshold for medium graphs.
        graph_guidance_threshold_large: The confidence threshold for large graphs.
    """

    graph_guidance_threshold_small: float
    graph_guidance_threshold_medium: float
    graph_guidance_threshold_large: float


class _GraphTraversalConfig(Protocol):
    """Minimal config surface consumed by traversal heuristics.

    Attributes:
        graph_size_small_edges: The number of edges considered small.
        graph_size_large_edges: The number of edges considered large.
        graph_depth_small_default: The default traversal depth for small graphs.
        graph_depth_small_max: The maximum traversal depth for small graphs.
        graph_depth_medium_default: The default traversal depth for medium graphs.
        graph_depth_medium_max: The maximum traversal depth for medium graphs.
        graph_depth_large_default: The default traversal depth for large graphs.
        graph_depth_large_max: The maximum traversal depth for large graphs.
        graph_fanout_depth1: The fan-out cap for depth 1 traversal.
        graph_fanout_depth2: The fan-out cap for depth 2 traversal.
        graph_fanout_depth3_plus: The fan-out cap for depth 3+ traversal.
        graph_guidance_threshold_small: The confidence threshold for small graphs.
        graph_guidance_threshold_medium: The confidence threshold for medium graphs.
        graph_guidance_threshold_large: The confidence threshold for large graphs.
        graph_min_marginal_new_node_gain_pct: The minimum marginal gain percentage to continue traversal.
        graph_traversal_max_edges: The maximum number of edges to traverse.
        graph_traversal_max_payload_bytes: The maximum payload size in bytes for traversal.
    """

    graph_size_small_edges: int
    graph_size_large_edges: int

    graph_depth_small_default: int
    graph_depth_small_max: int
    graph_depth_medium_default: int
    graph_depth_medium_max: int
    graph_depth_large_default: int
    graph_depth_large_max: int

    graph_fanout_depth1: int
    graph_fanout_depth2: int
    graph_fanout_depth3_plus: int

    graph_guidance_threshold_small: float
    graph_guidance_threshold_medium: float
    graph_guidance_threshold_large: float

    graph_min_marginal_new_node_gain_pct: float
    graph_traversal_max_edges: int
    graph_traversal_max_payload_bytes: int


def _edge_attr(edge: Any, key: str, default: Any = None) -> Any:
    """Read edge attribute from dict-like or object-like values.

    Args:
        edge: The edge object or dictionary.
        key: The attribute key to retrieve.
        default: The default value to return if the key is not found.

    Returns:
        The value of the attribute or the default if not found.
    """

    if isinstance(edge, dict):
        return edge.get(key, default)
    return getattr(edge, key, default)


def graph_size_band(*, edge_count: int, config: _GraphSizeBandConfig) -> GraphSizeBand:
    """Classify graph size band from configured edge thresholds.

    Args:
        edge_count: The number of edges in the graph.
        config: The configuration containing size thresholds.

    Returns:
        The size band of the graph ("small", "medium", or "large").
    """

    if edge_count < config.graph_size_small_edges:
        return "small"
    if edge_count <= config.graph_size_large_edges:
        return "medium"
    return "large"


def traversal_depth_limits(
    *,
    edge_count: int,
    config: _GraphTraversalConfig,
) -> tuple[int, int]:
    """Return (default_depth, max_depth) for the current graph size band.

    Args:
        edge_count: The number of edges in the graph.
        config: The configuration containing depth limits.

    Returns:
        A tuple of (default_depth, max_depth) for the graph size band.
    """

    size = graph_size_band(edge_count=edge_count, config=config)
    if size == "small":
        return (config.graph_depth_small_default, config.graph_depth_small_max)
    if size == "medium":
        return (config.graph_depth_medium_default, config.graph_depth_medium_max)
    return (config.graph_depth_large_default, config.graph_depth_large_max)


def resolve_requested_depth(
    *,
    edge_count: int,
    requested_depth: int | None,
    config: _GraphTraversalConfig,
) -> int:
    """Resolve effective traversal depth from request with clamped max.

    Args:
        edge_count: The number of edges in the graph.
        requested_depth: The requested traversal depth, or None for default.
        config: The configuration containing depth limits.

    Returns:
        The effective traversal depth, clamped to the maximum allowed.
    """

    default_depth, max_depth = traversal_depth_limits(edge_count=edge_count, config=config)
    if requested_depth is None:
        return default_depth
    return max(1, min(int(requested_depth), max_depth))


def fanout_limit_for_depth(*, depth: int, config: _GraphTraversalConfig) -> int:
    """Return fan-out cap for traversal hop depth.

    Args:
        depth: The current traversal depth (1-based).
        config: The configuration containing fan-out limits.

    Returns:
        The fan-out cap for the given depth.
    """

    if depth <= 1:
        return config.graph_fanout_depth1
    if depth == 2:
        return config.graph_fanout_depth2
    return config.graph_fanout_depth3_plus


def guidance_threshold(*, edge_count: int, config: _GraphGuidanceThresholdConfig) -> float:
    """Return confidence threshold for inferred guidance edges.

    Args:
        edge_count: The number of edges in the graph.
        config: The configuration containing guidance thresholds.

    Returns:
        The confidence threshold for inferred guidance edges.
    """

    size = graph_size_band(edge_count=edge_count, config=config)
    if size == "small":
        return config.graph_guidance_threshold_small
    if size == "medium":
        return config.graph_guidance_threshold_medium
    return config.graph_guidance_threshold_large


def marginal_gain_pct(*, existing_node_count: int, new_node_count: int) -> float:
    """Compute marginal node-growth percentage for early-stop checks.

    Args:
        existing_node_count: The number of nodes already traversed.
        new_node_count: The number of new nodes discovered in the current hop.

    Returns:
        The marginal node-growth percentage.
    """

    if existing_node_count <= 0:
        return 100.0 if new_node_count > 0 else 0.0
    return (float(new_node_count) / float(existing_node_count)) * 100.0


def should_stop_for_marginal_gain(
    *,
    existing_node_count: int,
    new_node_count: int,
    config: _GraphTraversalConfig,
) -> bool:
    """Return True when traversal should stop due to low marginal growth.

    Args:
        existing_node_count: The number of nodes already traversed.
        new_node_count: The number of new nodes discovered in the current hop.
        config: The configuration containing the minimum marginal gain percentage.

    Returns:
        True if traversal should stop due to low marginal growth, False otherwise.
    """

    return marginal_gain_pct(
        existing_node_count=existing_node_count,
        new_node_count=new_node_count,
    ) < float(config.graph_min_marginal_new_node_gain_pct)


def select_edges_for_hop(
    *,
    edges: list[Any],
    depth: int,
    edge_count: int,
    config: _GraphTraversalConfig,
) -> list[Any]:
    """Select edges for a traversal hop with structural preservation and pruning.

    Rules:
    - Always keep structural edges.
    - For inferred guidance edges, apply size-aware confidence threshold.
    - Apply per-hop fan-out caps, prioritizing higher confidence.

    Args:
        edges: The list of edges to select from.
        depth: The current traversal depth (1-based).
        edge_count: The total number of edges in the graph.
        config: The configuration containing thresholds and fan-out limits.

    Returns:
        A list of selected edges for the current hop.
    """

    if not edges:
        return []

    threshold = guidance_threshold(edge_count=edge_count, config=config)
    cap = fanout_limit_for_depth(depth=depth, config=config)

    structural: list[Any] = []
    inferred: list[Any] = []

    for edge in edges:
        edge_type_raw = str(_edge_attr(edge, "edge_type", "")).strip().upper()
        confidence = float(_edge_attr(edge, "confidence", 1.0) or 0.0)
        is_structural = edge_type_raw in {edge_type.value for edge_type in STRUCTURAL_EDGE_TYPES}
        if is_structural:
            structural.append(edge)
            continue

        if (
            edge_type_raw == GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL.value
            and confidence < threshold
        ):
            continue
        inferred.append(edge)

    inferred_sorted = sorted(
        inferred,
        key=lambda edge: float(_edge_attr(edge, "confidence", 0.0) or 0.0),
        reverse=True,
    )
    selected = structural + inferred_sorted[: max(0, cap)]
    return selected


def traversal_truncation_metadata(
    *,
    traversed_edges: int,
    payload_bytes: int,
    config: _GraphTraversalConfig,
) -> dict[str, Any]:
    """Return truncation metadata for graph responses and audit payloads.

    Args:
        traversed_edges: The number of edges traversed in the query.
        payload_bytes: The size of the response payload in bytes.
        config: The configuration containing traversal limits.

    Returns:
        A dictionary containing truncation metadata.
    """

    truncated_reasons: list[str] = []
    if traversed_edges > config.graph_traversal_max_edges:
        truncated_reasons.append("max_edges")
    if payload_bytes > config.graph_traversal_max_payload_bytes:
        truncated_reasons.append("max_payload_bytes")

    return {
        "truncated": bool(truncated_reasons),
        "truncation_reasons": truncated_reasons,
        "traversed_edges": traversed_edges,
        "payload_bytes": payload_bytes,
        "max_edges": config.graph_traversal_max_edges,
        "max_payload_bytes": config.graph_traversal_max_payload_bytes,
    }
