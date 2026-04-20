# Storage Abstraction Contract

## Purpose
Expose a common storage interface for blob/object operations used by ingestion and retrieval flows.

## Proposed Capabilities
- Put object/blob content.
- Get object/blob metadata.
- List object/blob keys by prefix.
- Delete object/blob key.
- Build object/blob reference URL when needed.

## Design Constraints
- Azure behaviour remains current baseline.
- AWS implementation targets S3 semantics.
- Local implementation uses filesystem-backed storage for tests/dev.

## Factory Pattern
- A storage factory creates provider adapters from runtime config.
- Business logic receives a storage interface, not SDK-specific clients.

## Test Requirements
- Contract tests shared across provider adapters.
- Round-trip tests for create, read/list, metadata, delete.
- Error mapping tests to ensure consistent exceptions across providers.
