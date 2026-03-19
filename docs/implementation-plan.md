# Implementation Plan

## 1. Objectives

Build an end-to-end, private-network Azure AI Agent platform by code only, using Terraform for infrastructure and Python for runtime orchestration.

Operational convention:

- Use `TARGET_ENV` in runbooks and shell examples to refer to the selected environment.
- Treat `infra/terraform/environments/<env>/<env>.tfvars` as the authoritative environment configuration.
- Roll out runtime containers with immutable image tags through Terraform, rather than direct Container App image mutation.

## 2. Assumptions and Constraints

- The tenant may be empty; all platform components are created through Terraform.
- Where a free Azure tenant already includes a default subscription, that subscription is used.
- Agent capabilities are Azure-hosted AI Agent infrastructure, while supporting runtime services (ingestion and query web) are self-managed Azure Container Apps workloads.
- Public network access is disabled for supported data and AI services.
- Documentation and internal naming use UK English unless platform APIs require specific values.
- Bastion and jumpbox are optional operational access patterns intended for standalone isolated deployments (for example sandbox Foundry validation) and for small or medium organisations without mature private network controls.
- In larger enterprise environments with mature private networking and governed access paths, Bastion and jumpbox are not normally required and are expected to be replaced by enterprise access patterns.

## 3. Functional Outcomes

- Ingest workflow supporting PDF and Excel sources with repeatable chunking and index update behaviour.
- Query workflow supporting conversational RAG with hybrid retrieval and reranking.
- Configurable default models:
  - Embedding model: `text-embedding-ada-002`
  - Query model: `gpt-5.1-chat`
  - Query evaluation model: `gpt-4.1-mini`
- Runtime behaviour outcomes:
  - Identity-based access to Storage, Search, Foundry, and Cosmos without static secrets in code.
  - Traceable request path with correlated logs, metrics, and traces for ingest and query execution.
  - Environment-driven configuration so model names, index settings, and feature flags can be changed without code edits.
- Outcome validation signals:
  - Sample ingestion run produces indexed content from both PDF and Excel fixtures.
  - Query run returns grounded responses with retrievable evidence references.
  - Evaluation path can execute against configured evaluation model and emit structured result records.

### 3.1 Functional Breakdown

1. Ingest workflow
  - Source adapters for PDF and Excel fixtures.
  - Deterministic chunking strategy with configurable chunk size/overlap.
  - Upsert flow into Azure AI Search with idempotent document keys.
  - Run summary output: processed files, chunk count, skipped/failed files.
2. Query workflow
  - Request contract for conversation context, user query, and retrieval options.
  - Hybrid retrieval + reranking path with configurable top-k limits.
  - Grounded response payload including evidence references.
  - Fallback and error handling for empty retrieval and model timeouts.
3. Evaluation workflow
  - Optional evaluation execution against configured evaluation model.
  - Structured evaluation output persisted for review (score/reason/result metadata).
  - Feature flag to enable/disable evaluation in runtime environments.
4. Identity and configuration
  - Managed identity authentication only for runtime-to-service calls.
  - Environment-driven configuration for models, index names, and feature flags.
  - No static credentials embedded in runtime code.

## 4. Infrastructure Outcomes

- Resource group and private network topology.
- Private endpoint subnet and delegated agent subnet.
- Storage account with blob container `grounding-data`.
- Azure AI Search.
- Azure AI Foundry resources and hosted agent capability components.
- Azure Cosmos DB.
- Optional jumpbox VM with Bastion access for standalone/sandbox and SMB-style deployments.
- Private DNS zones and conditional forwarder rules for:
  - `privatelink.blob.core.windows.net`
  - `privatelink.cognitiveservices.azure.com`
  - `privatelink.documents.azure.com`
  - `privatelink.file.core.windows.net`
  - `privatelink.openai.azure.com`
  - `privatelink.search.windows.net`
  - `privatelink.services.ai.azure.com`
- Public network access disabled across supported resources.

## 5. Target Architecture

1. Foundation layer
  - Create subscription-scoped baseline resources where required.
  - Create resource group, virtual network, and subnets.
2. Network and name resolution layer
  - Provision private endpoint subnet (/24).
  - Provision delegated agent subnet (/24).
  - Optionally provision jumpbox subnet and Azure Bastion for standalone/sandbox and SMB deployments.
  - Create private DNS zones, links, and records through private endpoint associations.
  - Configure conditional forwarders in DNS virtual server operations workflow.
3. Data and AI services layer
  - Provision Storage, Search, Foundry, and Cosmos DB.
  - Enforce private endpoint connectivity and disable public network exposure.
4. Identity and access layer
  - Create user-assigned/system-assigned managed identities as needed.
  - Apply least-privilege RBAC for agent runtime, ingestion, query, and operations, including Cosmos DB data-plane access.
5. Runtime services layer
  - Implement ingestion and query workflows in Python runtime services.
  - Add telemetry, feedback persistence, and test harnesses.

## 6. Security Model

- Managed identities used wherever possible.
- Least-privilege RBAC assignments through Terraform.
- Private endpoint access for Storage, Search, Foundry, and Cosmos.
- Network-level isolation for agent runtime workloads.
- Optional Bastion-mediated access for operational administration when enterprise network access patterns are not available.
- No direct public ingress to private data-plane services.

## 7. Observability Model

- Central LAW for logs and diagnostics.
- OpenTelemetry SDK instrumentation in runtime services for traces/metrics/log correlation.
- Prometheus scrape-compatible metrics endpoint in runtime services.
- Azure Monitor metrics export path and alerts in later phases.

## 8. Testing Strategy Summary

- Unit tests run in CI without private network access.
- Integration tests that require private endpoints run from:
  - Jumpbox (standalone/sandbox convenience), or
  - self-hosted runner inside the private VNet (typical enterprise approach).
- Smoke tests verify DNS private resolution and data-plane reachability.

## 9. Terraform Delivery Structure

1. Bootstrap
  - Remote state prerequisites and backend resources.
  - Optional standalone demo Key Vault for jumpbox public-key publish workflow (toggleable).
  - Enterprise deployments are expected to replace this workflow with organisation-managed key lifecycle controls at the publish integration point in phase automation.
2. Root stack composition
  - Module orchestration from `infra/terraform/main.tf`.
3. Module responsibilities
  - `foundation`: resource groups and baseline controls.
  - `network`: VNet and subnet definitions.
  - `dns`: private DNS zone/linking baseline.
  - `data_services`: Storage, Search, Cosmos, and Foundry core resources.
  - `private_endpoints`: endpoint mappings and private DNS zone groups.
  - `identity`: managed identities and RBAC.
  - `agent_hosting`: hosted agent capability dependencies and bindings, plus Container Apps runtime service hosting.
  - `bastion_jumpbox`: optional jumpbox VM and Bastion path for standalone/sandbox and SMB deployments.
  - `observability`: LAW, diagnostics, and monitoring baselines.

## 10. Runtime Delivery Structure

1. Worker pattern
  - Ingest worker reads from source inputs, chunks documents, and updates search indices.
2. API pattern
  - Query API manages conversations and response generation over retrieved evidence.
3. Shared package
  - Model settings, identity-aware clients, telemetry helpers, and retry policies.

## 11. Delivery Phases

1. Bootstrap and Terraform runner
  - Build and validate Terraform container tooling.
  - Deploy remote state bootstrap.
2. Network and DNS foundation
  - Deploy VNet/subnets and private DNS foundations.
  - Confirm private name resolution pathways.
3. Core data and AI services
  - Deploy Storage, Search, Foundry, Cosmos.
  - Wire private endpoints and disable public access.
4. Hosted agent environment and identities
  - Deploy managed identities and role assignments.
  - Configure hosted AI agent runtime components.
5. Runtime workflows
  - Implement ingest and query workflows.
  - Integrate default model configuration, conversation persistence, response ratings, and evaluation path.
  - Deliver local smoke workflow execution path and baseline runtime tests.
6. Verification and hardening
  - Execute private-network integration tests.
  - Add alerting baselines and operational checks.

## 12. Definition of Done

- Infrastructure deploys successfully from a clean environment via Terraform runner.
- All required services are reachable privately and blocked publicly.
- Managed identity authentication works across runtime-to-service interactions.
- Unit tests pass in standard CI.
- Integration tests pass from private network execution location.
- Baseline logs, traces, and metrics are visible in the observability stack.

## 13. Current Non-Goals

- Full Terraform policy-as-code stack and enterprise policy exemption workflow.
- Full CI/CD release automation and promotion workflow.
- Complete incident-response and on-call operational runbooks.
