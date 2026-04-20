# Search Abstraction Contract

## Purpose
Define a cloud-neutral search interface supporting hybrid retrieval used by query and assessment pipelines.

## Proposed Capabilities
- Text search with optional filters.
- Vector search with configurable top-k.
- Optional combined/hybrid search mode.
- Result normalisation into provider-neutral document shape.

## Design Constraints
- Keep existing Azure AI Search behaviour as default baseline.
- Allow OpenSearch implementation with equivalent output semantics.
- Keep retrieval scoring and metadata fields normalised.

## Factory Pattern
- A search factory resolves provider adapter based on configuration.
- Downstream pipeline code consumes normalised search responses.

## Test Requirements
- Contract tests validating result shape and ranking fields.
- Provider adapter tests for parameter mapping.
- Regression tests for hybrid retrieval behaviour.
