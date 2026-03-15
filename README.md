# Hosted AI Cyber Safety Platform

Infrastructure-as-Code and runtime scaffold for a privately secured Azure AI Agent platform.

## Scope

- Uses Azure-hosted Agent Service (not self-managed Azure Container Apps for the agent runtime).
- Deploys all platform resources via Terraform.
- Secures data plane services by private endpoints with public network access disabled.
- Supports ingestion and query agent workflows with configurable model defaults.

## Delivery Principles

- Code-only provisioning from an empty Azure tenant assumption.
- UK English documentation and naming conventions where platform APIs allow.
- Generic platform naming without legacy brand references.
- Managed identities and least-privilege access by default.
- Private networking first, with public network access disabled on supported services.

## What This Repository Contains

- Terraform runner container for deterministic infrastructure operations.
- Modular Terraform layout for foundation, network, data services, private endpoints, observability, and agent hosting.
- Private networking model with:
  - VNet `/16`
  - Private endpoint subnet `/24`
  - Delegated agent subnet `/24`
  - Jumpbox subnet and Bastion host
- Azure platform resources:
  - Storage account with blob container `grounding-data`
  - Azure AI Search
  - Azure AI Foundry resource and hosted agent runtime components
  - Azure Cosmos DB
- Private endpoint DNS zones and conditional forwarder guidance for:
  - `privatelink.blob.core.windows.net`
  - `privatelink.cognitiveservices.azure.com`
  - `privatelink.documents.azure.com`
  - `privatelink.file.core.windows.net`
  - `privatelink.openai.azure.com`
  - `privatelink.search.windows.net`
  - `privatelink.services.ai.azure.com`
- Logging and metrics foundations:
  - Log Analytics Workspace (LAW)
  - OpenTelemetry instrumentation baseline for runtime services
  - Prometheus-compatible metrics endpoint guidance with Azure Monitor integration path
- Unit-testing-first approach, with private-network integration tests designed to run from trusted network locations.

## Repository Layout

- `docs/` architecture, plans, and runbooks
- `infra/terraform/` bootstrap, environments, and reusable modules
- `ops/containers/terraform-runner/` Terraform execution container
- `runtime/` application skeleton for API and workers
- `tests/` unit and integration tests

## Runtime Functional Targets

- Ingest agent:
  - Chunks and indexes PDF and Excel sources.
  - Uses AI agent file search tooling patterns.
- Query agent:
  - Supports conversational retrieval-augmented generation.
  - Uses hybrid retrieval with reranking.
- Default model values (configurable):
  - Embedding model: `text-embedding-ada-002`
  - Query model: `gpt-5.1-chat`
  - Query evaluation model: `gpt-4.1-mini`

## Environment and Subscription Assumptions

- If an Azure free tenant already has a default subscription, that subscription is used.
- If no subscription context is available in automation, deployment fails fast with explicit guidance.
- Resource groups, networking, identities, and services are created by Terraform only.

## Quick Start

1. Build the Terraform runner image:
   - `docker build -t tf-runner:local ops/containers/terraform-runner`
2. If Docker is unavailable in your working container, install Terraform locally:
  - `./ops/scripts/install-terraform-local.sh`
3. Configure environment-specific Terraform variables in `infra/terraform/environments/dev/dev.tfvars`.
4. Deploy bootstrap (state backend) first.
5. Deploy platform stack in phased order documented in `docs/implementation-plan.md`.

## Documentation Index

- Detailed implementation sequencing: `docs/implementation-plan.md`
- Delivery slicing by phase: `docs/phases.md`
- Testing policy and private-endpoint test execution: `docs/testing-strategy.md`
- Logging and metrics baseline: `docs/observability.md`

## Current State

This repository contains a working scaffold and delivery plan. Module-level implementation and runtime build-out continue in phased increments, with private networking and security controls treated as non-negotiable constraints.
