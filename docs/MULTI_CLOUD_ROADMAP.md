# Multi-Cloud Roadmap (Azure + AWS + Local)

## Scope
- Keep one codebase.
- Preserve current Azure production behaviour.
- Add AWS support with feature parity.
- Keep local development first-class (Ollama and optional Llama.cpp).
- Improve production quality: observability, CI quality gates, schema versioning.

## Delivery Model
- Team size: 1-2 engineers.
- Timeline: 12-13 weeks.
- CI: self-hosted runner in private network.
- MCP externalisation: deferred until core platform stabilises.

## Phases

### Phase 0 (Week 1): Preparation
- Document abstraction contracts for credential, storage, search, and state store.
- Decide CI runner placement and network path to private resources.
- Define local LLM decision criteria (quality, latency, memory, operational overhead).
- Publish delivery checkpoints and acceptance criteria.

### Phase 1 (Weeks 2-4): Abstraction Layer
- Introduce Protocols and factories for cloud-dependent clients.
- Keep behaviour unchanged for Azure path.
- Add abstraction compliance tests.

Exit criteria:
- Existing unit tests stay green.
- New abstraction tests are in place.
- Azure path remains default and fully working.

### Phase 2 (Weeks 5-6): app.py Refactor
- Reduce app entrypoint responsibilities.
- Move endpoint logic to dedicated modules in query_web/endpoints.
- Remove module-global service patching pattern and use explicit dependency injection.

Exit criteria:
- app.py is focused on startup/config wiring.
- Endpoint modules are independently testable.

### Phase 3 (Weeks 7-9): AWS Provider Support
- Add AWS Terraform stack under infra/terraform/aws.
- Implement AWS-backed search, state store, and LLM runtime adapters.
- Add parity smoke tests for ingest, query, and assessment flows.

Exit criteria:
- Azure and AWS execute the same business logic via provider adapters.
- Basic end-to-end smoke tests pass in AWS dev environment.

### Phase 4 (Weeks 10-11): OpenTelemetry and Metrics
- Instrument key request, retrieval, and assessment paths.
- Add trace correlation in logs.
- Add runtime metrics endpoint and exporter configuration.

Exit criteria:
- Trace and metric signals are visible in configured backend.
- Logs include trace context for major workflows.

### Phase 5 (Weeks 11-12): CI Quality Gates
- Add self-hosted CI workflow for lint, type checks, tests, security checks, and Terraform validation.
- Enforce branch protection with required checks.

Exit criteria:
- PR merges are blocked when quality gates fail.
- CI can run private-network integration checks where required.

### Phase 6 (Weeks 12-13, Optional): Schema Versioning
- Add explicit schema version fields for key persisted entities.
- Add migration framework and migration CLI.

Exit criteria:
- Backward/forward compatibility policy documented and tested.

## Risks
- AWS OpenSearch parity and indexing semantics are the largest technical risk.
- Private-network CI runner topology can delay integration testing if not decided early.
- Refactor churn in app.py can create short-term merge friction if done without tight test discipline.

## Current Week Checklist (Phase 0)
- [x] Create roadmap doc.
- [x] Create abstraction contract stubs.
- [x] Create CI self-hosted runner setup stub.
- [x] Create local LLM comparison doc.
- [ ] Confirm CI platform and runner placement.
- [ ] Lock initial module boundaries for Phase 1 implementation PR.
