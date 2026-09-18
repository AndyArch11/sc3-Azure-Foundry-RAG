"""Graph artifact schemas and record builders for batch graph generation.

- Define node/edge artifact schemas with graph schema versioning.
- Provide deterministic node record builders for Corpus A and Corpus B.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from query_web.constants import GRAPH_SCHEMA_VERSION
from query_web.graph_foundation import (
    GraphEdgeType,
    GraphNodeType,
    build_corpus_a_node_id,
    build_corpus_b_node_id,
    build_edge_dedupe_key,
)


def _clean(value: Any) -> str:
    """Convert arbitrary value to trimmed string.

    Args:
        value: Any value to be converted to string.
    Returns:
        A trimmed string representation of the input value. If the value is None, returns an empty string.
    """

    return str(value or "").strip()


def _stable_node_id(prefix: str, raw_key: str) -> str:
    """Build a stable node identifier from a prefix and raw key.

    Args:
        prefix: The prefix for the node identifier.
        raw_key: The raw key to be hashed.

    Returns:
        A stable node identifier string.
    """

    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


class GraphNodeRecord(BaseModel):
    """Serialised node artifact record for graph build outputs.

    Attributes:
        graph_schema_version (str): The version of the graph schema.
        node_id (str): The unique identifier for the node.
        node_type (GraphNodeType): The type of the node.
        label (str): A human-readable label for the node.
        attributes (dict[str, Any]): A dictionary of additional attributes associated with the node.
    """

    graph_schema_version: str = Field(default=GRAPH_SCHEMA_VERSION, min_length=1, max_length=32)
    node_id: str = Field(min_length=1)
    node_type: GraphNodeType
    label: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeRecord(BaseModel):
    """Serialised edge artifact record for graph build outputs.

    Attributes:
        graph_schema_version (str): The version of the graph schema.
        edge_id (str): The unique identifier for the edge.
        from_id (str): The identifier of the source node.
        to_id (str): The identifier of the target node.
        edge_type (GraphEdgeType): The type of the edge.
        confidence (float): The confidence score of the edge.
        evidence_key (str): The key for the evidence supporting the edge.
        attributes (dict[str, Any]): A dictionary of additional attributes associated with the edge.
    """

    graph_schema_version: str = Field(default=GRAPH_SCHEMA_VERSION, min_length=1, max_length=32)
    edge_id: str = Field(min_length=1)
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    edge_type: GraphEdgeType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_key: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


def build_corpus_a_control_node(control: dict[str, Any]) -> GraphNodeRecord:
    """Build a graph node record for a Corpus A control.

    Expected fields include requirement_id, framework, framework_version,
    control_family, maturity_level, requirement_text, guidance_text, source_uri.

    Args:
        control: A dictionary representing a Corpus A control with its associated metadata.

    Returns:
        A GraphNodeRecord representing the Corpus A control.
    """

    requirement_id = _clean(control.get("requirement_id"))
    node_id = build_corpus_a_node_id(requirement_id)
    return GraphNodeRecord(
        node_id=node_id,
        node_type=GraphNodeType.CORPUS_A_CONTROL,
        label=requirement_id,
        attributes={
            "requirement_id": requirement_id,
            "framework": _clean(control.get("framework")),
            "framework_version": _clean(control.get("framework_version")),
            "control_family": _clean(control.get("control_family")),
            "maturity_level": control.get("maturity_level"),
            "requirement_text": _clean(control.get("requirement_text")),
            "guidance_text": _clean(control.get("guidance_text")),
            "source_uri": _clean(control.get("source_uri")),
        },
    )


def build_corpus_b_guidance_node(
    chunk: dict[str, Any],
    *,
    chunk_ordinal: int | None = None,
) -> GraphNodeRecord:
    """Build a graph node record for a Corpus B guidance chunk.

    Expected fields include source_path, source_name, normalised_text_sha256,
    content_sha256, corpus, corpus_role, and content.

    Args:
        chunk: A dictionary representing a Corpus B guidance chunk with its associated metadata.
        chunk_ordinal: An optional ordinal number for the chunk.

    Returns:
        A GraphNodeRecord representing the Corpus B guidance chunk.
    """

    source_name = _clean(chunk.get("source_name"))
    source_path = _clean(chunk.get("source_path"))
    normalised_hash = _clean(chunk.get("normalised_text_sha256"))
    content_hash = _clean(chunk.get("content_sha256"))

    node_id = build_corpus_b_node_id(
        source_path=source_path,
        source_name=source_name,
        normalised_text_sha256=normalised_hash,
        content_sha256=content_hash,
        chunk_ordinal=chunk_ordinal,
    )
    label = _clean(chunk.get("original_filename")) or source_name or source_path or node_id

    return GraphNodeRecord(
        node_id=node_id,
        node_type=GraphNodeType.CORPUS_B_GUIDANCE_CHUNK,
        label=label,
        attributes={
            "source_name": source_name,
            "source_path": source_path,
            "original_filename": _clean(chunk.get("original_filename")),
            "normalised_text_sha256": normalised_hash,
            "content_sha256": content_hash,
            "corpus": _clean(chunk.get("corpus")).lower(),
            "corpus_role": _clean(chunk.get("corpus_role")).lower(),
            "content": _clean(chunk.get("content")),
            "score": float(chunk.get("score") or 0.0),
        },
    )


def build_graph_edge_record(
    *,
    from_id: str,
    to_id: str,
    edge_type: GraphEdgeType,
    evidence_key: str = "",
    confidence: float = 1.0,
    attributes: dict[str, Any] | None = None,
) -> GraphEdgeRecord:
    """Build a canonical edge record with deterministic edge ID.

    Args:
        from_id: The identifier of the source node.
        to_id: The identifier of the target node.
        edge_type: The type of the edge.
        evidence_key: The key for the evidence supporting the edge.
        confidence: The confidence score of the edge.
        attributes: A dictionary of additional attributes associated with the edge.

    Returns:
        A GraphEdgeRecord representing the edge.
    """

    edge_id = build_edge_dedupe_key(
        from_id=from_id,
        edge_type=edge_type,
        to_id=to_id,
        evidence_key=evidence_key,
    )
    return GraphEdgeRecord(
        edge_id=edge_id,
        from_id=from_id,
        to_id=to_id,
        edge_type=edge_type,
        evidence_key=evidence_key,
        confidence=confidence,
        attributes=attributes or {},
    )


def build_framework_node(framework: str) -> GraphNodeRecord:
    """Build a framework node from a canonical framework name.

    Args:
        framework: The canonical name of the framework (e.g., "CIS", "NIST").
    Returns:
        A GraphNodeRecord representing the framework.
    """

    name = _clean(framework)
    if not name:
        raise ValueError("framework must be non-empty")
    return GraphNodeRecord(
        node_id=_stable_node_id("framework", name.lower()),
        node_type=GraphNodeType.FRAMEWORK,
        label=name,
        attributes={"framework": name},
    )


def build_control_family_node(*, framework: str, control_family: str) -> GraphNodeRecord:
    """Build a control-family node scoped by framework.

    Args:
        framework: The canonical name of the framework (e.g., "CIS", "NIST").
        control_family: The name of the control family.

    Returns:
        A GraphNodeRecord representing the control family.
    """

    fw = _clean(framework)
    family = _clean(control_family)
    if not family:
        raise ValueError("control_family must be non-empty")
    key = f"{fw.lower()}|{family.lower()}"
    return GraphNodeRecord(
        node_id=_stable_node_id("family", key),
        node_type=GraphNodeType.CONTROL_FAMILY,
        label=family,
        attributes={"framework": fw, "control_family": family},
    )


def build_source_document_node(
    *, source_uri: str = "", source_path: str = "", source_name: str = ""
) -> GraphNodeRecord:
    """Build a source-document node from URI/path/name metadata.

    Args:
        source_uri: The URI of the source document.
        source_path: The file path of the source document.
        source_name: The name of the source document.

    Returns:
        A GraphNodeRecord representing the source document.
    """

    uri = _clean(source_uri)
    path = _clean(source_path)
    name = _clean(source_name)
    source_key = uri or path or name
    if not source_key:
        raise ValueError("source_uri, source_path, or source_name must be non-empty")
    return GraphNodeRecord(
        node_id=_stable_node_id("source", source_key.lower()),
        node_type=GraphNodeType.SOURCE_DOCUMENT,
        label=name or path or uri,
        attributes={"source_uri": uri, "source_path": path, "source_name": name},
    )


def build_structural_records_for_corpus_a_control(
    control_node: GraphNodeRecord,
) -> tuple[list[GraphNodeRecord], list[GraphEdgeRecord]]:
    """Build structural framework/family/source records linked from a control node.

    Args:
        control_node: The control node from which to build structural records.

    Returns:
        A tuple containing a list of GraphNodeRecords and a list of GraphEdgeRecords.
    """

    if control_node.node_type != GraphNodeType.CORPUS_A_CONTROL:
        raise ValueError("control_node must be CorpusAControl")

    attrs = control_node.attributes
    framework = _clean(attrs.get("framework"))
    family = _clean(attrs.get("control_family"))
    source_uri = _clean(attrs.get("source_uri"))

    nodes: list[GraphNodeRecord] = []
    edges: list[GraphEdgeRecord] = []

    if framework:
        framework_node = build_framework_node(framework)
        nodes.append(framework_node)
        edges.append(
            build_graph_edge_record(
                from_id=control_node.node_id,
                to_id=framework_node.node_id,
                edge_type=GraphEdgeType.BELONGS_TO_FRAMEWORK,
            )
        )

    if family:
        family_node = build_control_family_node(framework=framework, control_family=family)
        nodes.append(family_node)
        edges.append(
            build_graph_edge_record(
                from_id=control_node.node_id,
                to_id=family_node.node_id,
                edge_type=GraphEdgeType.IN_FAMILY,
            )
        )

    if source_uri:
        source_node = build_source_document_node(source_uri=source_uri)
        nodes.append(source_node)
        edges.append(
            build_graph_edge_record(
                from_id=control_node.node_id,
                to_id=source_node.node_id,
                edge_type=GraphEdgeType.DERIVED_FROM_SOURCE,
            )
        )

    return (nodes, edges)


def build_structural_records_for_corpus_b_guidance(
    guidance_node: GraphNodeRecord,
) -> tuple[list[GraphNodeRecord], list[GraphEdgeRecord]]:
    """Build structural source records linked from a guidance node.

    Args:
        guidance_node: The guidance node from which to build structural records.

    Returns:
        A tuple containing a list of GraphNodeRecords and a list of GraphEdgeRecords.
    """

    if guidance_node.node_type != GraphNodeType.CORPUS_B_GUIDANCE_CHUNK:
        raise ValueError("guidance_node must be CorpusBGuidanceChunk")

    attrs = guidance_node.attributes
    source_uri = _clean(attrs.get("source_uri"))
    source_path = _clean(attrs.get("source_path"))
    source_name = _clean(attrs.get("source_name"))

    if not (source_uri or source_path or source_name):
        return ([], [])

    source_node = build_source_document_node(
        source_uri=source_uri,
        source_path=source_path,
        source_name=source_name,
    )
    edge = build_graph_edge_record(
        from_id=guidance_node.node_id,
        to_id=source_node.node_id,
        edge_type=GraphEdgeType.DERIVED_FROM_SOURCE,
    )
    return ([source_node], [edge])


def build_guidance_support_edge(
    *,
    control_node: GraphNodeRecord,
    guidance_node: GraphNodeRecord,
    evidence_key: str,
    confidence: float,
    attributes: dict[str, Any] | None = None,
) -> GraphEdgeRecord:
    """Build an inferred Corpus B guidance supports Corpus A control edge.

    Args:
        control_node: The control node that is supported by the guidance.
        guidance_node: The guidance node that supports the control.
        evidence_key: The key representing the evidence for the support.
        confidence: The confidence level of the support.
        attributes: Optional additional attributes for the edge.

    Returns:
        A GraphEdgeRecord representing the support relationship.
    """

    if control_node.node_type != GraphNodeType.CORPUS_A_CONTROL:
        raise ValueError("control_node must be CorpusAControl")
    if guidance_node.node_type != GraphNodeType.CORPUS_B_GUIDANCE_CHUNK:
        raise ValueError("guidance_node must be CorpusBGuidanceChunk")

    return build_graph_edge_record(
        from_id=guidance_node.node_id,
        to_id=control_node.node_id,
        edge_type=GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        evidence_key=evidence_key,
        confidence=confidence,
        attributes=attributes,
    )


def dedupe_edge_records(edges: list[GraphEdgeRecord]) -> list[GraphEdgeRecord]:
    """Deduplicate edge records by edge_id, keeping highest-confidence variant.

    Ties are resolved deterministically by preserving the first-seen record.
    Returned edges are sorted by edge_id for stable artifact output order.

    Args:
        edges: A list of GraphEdgeRecords to deduplicate.

    Returns:
        A list of deduplicated GraphEdgeRecords.
    """

    by_id: dict[str, GraphEdgeRecord] = {}
    for edge in edges:
        existing = by_id.get(edge.edge_id)
        if existing is None or edge.confidence > existing.confidence:
            by_id[edge.edge_id] = edge

    return [by_id[key] for key in sorted(by_id.keys())]
