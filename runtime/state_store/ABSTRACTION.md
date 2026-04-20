# State Store Abstraction Contract

## Purpose
Provide a unified persistence interface for polling state, watermarking, leases, and assessment snapshots.

## Existing Baseline
- PollingStateStore Protocol exists in runtime/assessment_orchestration/state_store.py.

## Direction
- Keep protocol as source of truth.
- Add provider-backed implementations behind factory wiring:
  - Azure/Cosmos (current)
  - AWS/DynamoDB (planned)
  - Local file/in-memory (dev/testing)

## Design Constraints
- Preserve idempotency and optimistic concurrency semantics.
- Preserve lease behaviour for poller concurrency control.
- Keep serialisation compatible with schema versioning plan.

## Test Requirements
- Shared contract tests across implementations.
- Concurrency and lease behaviour tests.
- Backward-compatibility tests for persisted document shapes.
