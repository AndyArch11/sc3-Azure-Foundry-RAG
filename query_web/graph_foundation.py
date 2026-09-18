"""Foundation helpers for Corpus A/B relationship graph generation.

This module defines a stable ontology and deterministic keying rules used by
batch graph builders and graph APIs. It is intentionally provider-neutral so
the same node/edge IDs are produced in local, Azure, and AWS environments.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum


class GraphNodeType(StrEnum):
    """Canonical graph node types for the Corpus A/B relationship graph.

    Attributes:
        CORPUS_A_CONTROL: Represents a control node from Corpus A.
        CORPUS_B_GUIDANCE_CHUNK: Represents a guidance chunk node from Corpus B.
        FRAMEWORK: Represents a framework node.
        CONTROL_FAMILY: Represents a control family node.
        SOURCE_DOCUMENT: Represents a source document node.
    """

    CORPUS_A_CONTROL = "CorpusAControl"
    CORPUS_B_GUIDANCE_CHUNK = "CorpusBGuidanceChunk"
    FRAMEWORK = "Framework"
    CONTROL_FAMILY = "ControlFamily"
    SOURCE_DOCUMENT = "SourceDocument"


class GraphEdgeType(StrEnum):
    """Canonical graph edge types for the Corpus A/B relationship graph.

    Attributes:
        BELONGS_TO_FRAMEWORK: Represents an edge indicating a node belongs to a framework.
        IN_FAMILY: Represents an edge indicating a node is in a control family.
        DERIVED_FROM_SOURCE: Represents an edge indicating a node is derived from a source document.
        GUIDANCE_SUPPORTS_CONTROL: Represents an edge indicating guidance supports a control.
        GUIDANCE_RELATES_TO_FAMILY: Represents an edge indicating guidance relates to a control family.
    """

    BELONGS_TO_FRAMEWORK = "BELONGS_TO_FRAMEWORK"
    IN_FAMILY = "IN_FAMILY"
    DERIVED_FROM_SOURCE = "DERIVED_FROM_SOURCE"
    GUIDANCE_SUPPORTS_CONTROL = "GUIDANCE_SUPPORTS_CONTROL"
    GUIDANCE_RELATES_TO_FAMILY = "GUIDANCE_RELATES_TO_FAMILY"


STRUCTURAL_EDGE_TYPES: frozenset[GraphEdgeType] = frozenset(
    {
        GraphEdgeType.BELONGS_TO_FRAMEWORK,
        GraphEdgeType.IN_FAMILY,
        GraphEdgeType.DERIVED_FROM_SOURCE,
    }
)


INFERRED_EDGE_TYPES: frozenset[GraphEdgeType] = frozenset(
    {
        GraphEdgeType.GUIDANCE_SUPPORTS_CONTROL,
        GraphEdgeType.GUIDANCE_RELATES_TO_FAMILY,
    }
)


def _sha256_hex(text: str) -> str:
    """Return a stable SHA-256 digest for the provided text.

    Args:
        text: The input text to hash.

    Returns:
        A hexadecimal string representing the SHA-256 digest of the input text.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_corpus_a_node_id(requirement_id: str) -> str:
    """Build deterministic node ID for a Corpus A control node.

    Args:
        requirement_id: Canonical control identifier from parsed controls.

    Returns:
        Deterministic graph node ID.

    Raises:
        ValueError: If requirement_id is empty after trimming.
    """

    rid = requirement_id.strip()
    if not rid:
        raise ValueError("requirement_id must be non-empty")
    return f"a:{rid.lower()}"


def build_corpus_b_node_id(
    *,
    source_path: str,
    source_name: str,
    normalised_text_sha256: str,
    content_sha256: str,
    chunk_ordinal: int | None = None,
) -> str:
    """Build deterministic node ID for a Corpus B guidance chunk node.

    Args:
        source_path: Source path metadata if available.
        source_name: Source name metadata if available.
        normalised_text_sha256: Stable normalised content hash if available.
        content_sha256: Raw content hash fallback if available.
        chunk_ordinal: Optional chunk position within a source document.

    Returns:
        Deterministic graph node ID.

    Raises:
        ValueError: If no identifying metadata is provided.
    """

    source_part = source_path.strip() or source_name.strip()
    hash_part = normalised_text_sha256.strip() or content_sha256.strip()
    if not source_part and not hash_part and chunk_ordinal is None:
        raise ValueError("Corpus B node ID requires source/hash/ordinal metadata")

    ordinal_part = "" if chunk_ordinal is None else f":{chunk_ordinal}"
    raw_key = f"{source_part.lower()}|{hash_part.lower()}{ordinal_part}"
    return f"b:{_sha256_hex(raw_key)[:24]}"


def build_edge_dedupe_key(
    *,
    from_id: str,
    edge_type: GraphEdgeType | str,
    to_id: str,
    evidence_key: str = "",
) -> str:
    """Build idempotent edge key for dedupe across repeated graph runs.

    Args:
        from_id: Source node ID.
        edge_type: Canonical edge type.
        to_id: Destination node ID.
        evidence_key: Optional evidence discriminator (for tie-breaking).

    Returns:
        Deterministic edge dedupe key.
    """

    edge_value = str(edge_type).strip().upper()
    left = from_id.strip().lower()
    right = to_id.strip().lower()
    evidence = evidence_key.strip().lower()
    return f"{left}|{edge_value}|{right}|{_sha256_hex(evidence)[:16]}"
