# Relationship Graph Foundation

This document defines the foundation for the Corpus A/B relationship graph.

## Scope

- Included: Corpus A controls and Corpus B guidance chunks.
- Excluded in this phase: Corpus C graph modeling.

## Ontology

Node types:
- `CorpusAControl`
- `CorpusBGuidanceChunk`
- `Framework`
- `ControlFamily`
- `SourceDocument`

Edge types:
- Structural edges:
  - `BELONGS_TO_FRAMEWORK`
  - `IN_FAMILY`
  - `DERIVED_FROM_SOURCE`
- Inferred edges:
  - `GUIDANCE_SUPPORTS_CONTROL`
  - `GUIDANCE_RELATES_TO_FAMILY`

Structural edges are preserved during pruning; inferred edges are threshold-based.

## Deterministic ID Strategy

Corpus A node IDs:
- Format: `a:{requirement_id_lowercase}`
- Source: canonical `requirement_id` from parsed controls.

Corpus B node IDs:
- Format: `b:{sha256(source_or_name|hash|ordinal)[:24]}`
- Source priority:
  1. `source_path` (or fallback `source_name`)
  2. `normalised_text_sha256` (or fallback `content_sha256`)
  3. Optional `chunk_ordinal`

## Edge Dedupe Key Strategy

Deterministic dedupe key format:
- `{from_id_lower}|{EDGE_TYPE_UPPER}|{to_id_lower}|{sha256(evidence_key)[:16]}`

This key ensures idempotent edge writes across repeated graph builds.

## Graph Schema Versioning

- Current schema version constant: `GRAPH_SCHEMA_VERSION = "v1"`
- Environment override supported with `GRAPH_SCHEMA_VERSION`.
- Bump only when graph artifact or API graph shape changes.

## Config Flags and Thresholds

Milestone 1 introduces graph configuration in `QueryConfig`:

- Feature flag:
  - `GRAPH_ENABLED`
- Graph version:
  - `GRAPH_SCHEMA_VERSION`
- Size thresholds:
  - `GRAPH_SIZE_SMALL_EDGES` (default `50000`)
  - `GRAPH_SIZE_LARGE_EDGES` (default `250000`)
- Depth defaults and maxima:
  - `GRAPH_DEPTH_SMALL_DEFAULT`, `GRAPH_DEPTH_SMALL_MAX`
  - `GRAPH_DEPTH_MEDIUM_DEFAULT`, `GRAPH_DEPTH_MEDIUM_MAX`
  - `GRAPH_DEPTH_LARGE_DEFAULT`, `GRAPH_DEPTH_LARGE_MAX`
- Fan-out caps:
  - `GRAPH_FANOUT_DEPTH1`, `GRAPH_FANOUT_DEPTH2`, `GRAPH_FANOUT_DEPTH3_PLUS`
- Inferred-edge thresholds:
  - `GRAPH_GUIDANCE_THRESHOLD_SMALL`
  - `GRAPH_GUIDANCE_THRESHOLD_MEDIUM`
  - `GRAPH_GUIDANCE_THRESHOLD_LARGE`
- Safety and truncation controls:
  - `GRAPH_MIN_MARGINAL_NEW_NODE_GAIN_PCT`
  - `GRAPH_TRAVERSAL_MAX_EDGES`
  - `GRAPH_TRAVERSAL_MAX_PAYLOAD_BYTES`

## Rollout Priority

Implementation order is fixed:
1. Local
2. Azure
3. AWS

Initial Implementation is provider-neutral and does not require backend-specific graph storage. Future implementation may introduce cloud native solutions for managing relationship graphs.