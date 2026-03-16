# Delivery Phases and Artefacts

## Phase 1

- Terraform runner container
- Bootstrap state backend

## Phase 2

- Foundation/network modules
- DNS modules
- Optional Bastion and jumpbox module for standalone isolated/sandbox deployments and SMB scenarios without mature network controls

Note: In larger enterprise environments with mature private networking and controlled access paths, Bastion and jumpbox are typically not required.

## Phase 3

- Data services and private endpoints
- Foundry and agent hosting module wiring

## Phase 4

- Runtime API/worker skeleton
- Ingestion and query workflow scaffolding with shared configuration package
- Identity-first client wiring (no embedded secrets)
- Unit tests and telemetry baseline
- Local developer execution path for runtime smoke validation

### Phase 4 Workstreams

1. Runtime structure
  - Define API surface for query operations.
  - Define worker entry points for ingestion operations.
  - Establish shared configuration package for model/runtime settings.
2. Ingestion path
  - Implement PDF and Excel ingest adapters.
  - Implement chunking and indexing flow.
  - Add run summary output for ingestion jobs.
3. Query path
  - Implement hybrid retrieval and reranking orchestration.
  - Implement grounded response shaping with evidence references.
  - Add configurable query/evaluation model selection.
4. Identity and security
  - Implement managed-identity client initialization.
  - Validate no static credential usage in runtime paths.
5. Quality and observability
  - Add unit tests for configuration, chunking, retrieval, and response shaping.
  - Add telemetry baseline (trace/log correlation points).
  - Add local smoke path for ingest and query validation.

### Phase 4 Exit Criteria

- Runtime API and worker entry points can execute locally with environment-based config.
- Ingestion smoke run can process PDF and Excel fixtures and index results.
- Query smoke run returns grounded responses with evidence references.
- Unit test baseline passes in CI.
- Telemetry baseline emits traces/logs for both ingest and query flows.

## Phase 5

- Integration tests from private network runner
- Operational hardening

## Dependency Order

1. Complete Phase 1 before any other phase.
2. Complete core Phase 2 networking before Phase 3 data and AI services.
3. If used, Bastion and jumpbox are deployed in Phase 2 after VNet/subnets and before private-network integration testing.
4. Complete Phase 3 private endpoints and service wiring before starting Phase 4 runtime implementation against private services.
5. In Phase 4, implement workstreams in this order:
  - Runtime structure
  - Identity and security
  - Ingestion path
  - Query path
  - Quality and observability
6. Enter Phase 5 only after all Phase 4 exit criteria are met.
