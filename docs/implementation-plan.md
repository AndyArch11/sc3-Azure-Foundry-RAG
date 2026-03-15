# Implementation Plan

## 1. Objectives

Build an end-to-end, private-network Azure AI Agent platform by code only, using Terraform for infrastructure and Python for runtime orchestration.

## 2. Assumptions and Constraints

- The tenant may be empty; all platform components are created through Terraform.
- Where a free Azure tenant already includes a default subscription, that subscription is used.
- Agent runtime is Azure-hosted AI Agent infrastructure (not self-managed Azure Container Apps for the agent runtime).
- Public network access is disabled for supported data and AI services.
- Documentation and internal naming use UK English unless platform APIs require specific values.

## 3. Functional Outcomes

- Ingest agent for PDF and Excel sources.
- Query agent supporting conversational RAG with hybrid retrieval and reranking.
- Configurable default models:
  - Embedding model: `text-embedding-ada-002`
  - Query model: `gpt-5.1-chat`
  - Query evaluation model: `gpt-4.1-mini`

## 4. Infrastructure Outcomes

- Resource group and private network topology.
- Private endpoint subnet and delegated agent subnet.
- Storage account with blob container `grounding-data`.
- Azure AI Search.
- Azure AI Foundry resources and hosted agent runtime components.
- Azure Cosmos DB.
- Jumpbox VM with Bastion access.
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
  - Provision jumpbox subnet and Azure Bastion.
  - Create private DNS zones, links, and records through private endpoint associations.
  - Configure conditional forwarders in DNS virtual server operations workflow.
3. Data and AI services layer
  - Provision Storage, Search, Foundry, and Cosmos DB.
  - Enforce private endpoint connectivity and disable public network exposure.
4. Identity and access layer
  - Create user-assigned/system-assigned managed identities as needed.
  - Apply least-privilege RBAC for agent runtime, ingestion, query, and operations.
5. Runtime services layer
  - Implement ingestion and query workflows in Python runtime services.
  - Add telemetry and test harnesses.

## 6. Security Model

- Managed identities used wherever possible.
- Least-privilege RBAC assignments through Terraform.
- Private endpoint access for Storage, Search, Foundry, and Cosmos.
- Network-level isolation for agent runtime workloads.
- Bastion-mediated access for operational administration.
- No direct public ingress to private data-plane services.

## 7. Observability Model

- Central LAW for logs and diagnostics.
- OpenTelemetry SDK instrumentation in runtime services for traces/metrics/log correlation.
- Prometheus scrape-compatible metrics endpoint in runtime services.
- Azure Monitor metrics export path and alerts in later phases.

## 8. Testing Strategy Summary

- Unit tests run in CI without private network access.
- Integration tests that require private endpoints run from:
  - Jumpbox, or
  - self-hosted runner inside the private VNet.
- Smoke tests verify DNS private resolution and data-plane reachability.

## 9. Terraform Delivery Structure

1. Bootstrap
  - Remote state prerequisites and backend resources.
2. Root stack composition
  - Module orchestration from `infra/terraform/main.tf`.
3. Module responsibilities
  - `foundation`: resource groups and baseline controls.
  - `network`: VNet and subnet definitions.
  - `dns`: private DNS zone/linking baseline.
  - `data_services`: Storage, Search, Cosmos, and Foundry core resources.
  - `private_endpoints`: endpoint mappings and private DNS zone groups.
  - `identity`: managed identities and RBAC.
  - `agent_hosting`: hosted agent runtime dependencies and bindings.
  - `bastion_jumpbox`: jumpbox VM and Bastion path.
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
  - Integrate default model configuration and evaluation path.
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

## 13. Non-Goals for Scaffold Stage

- Full production-grade runtime implementation.
- Full Terraform policy set and CI release workflow.
- Complete runbooks for incident management.
