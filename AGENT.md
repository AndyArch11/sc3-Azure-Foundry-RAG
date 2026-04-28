# AGENT.md

## Purpose

This repository provisions and operates a privately networked Azure AI Foundry solution for a cyber security target persona
The agent should optimise for secure-by-default changes, deterministic infrastructure updates, and reproducible validation.

## Core Principles

- Terraform-first for all Azure resource lifecycle changes.
- Private networking is non-negotiable.
- Managed identity and least privilege by default.
- Prefer immutable container image tags, not latest.
- Keep changes minimal, targeted, and reversible.
- Use UK English in documentation updates and code comments where practical.

## Repository Orientation

- docs/: implementation, phases, observability, and testing strategy.
- infra/terraform/azure/: canonical Azure Terraform stack.
- infra/terraform/aws/: AWS Terraform stack.
- infra/terraform/: legacy Azure compatibility entrypoint during migration.
- ops/scripts/: operational scripts for phased applies and image workflows.
- query_web/: FastAPI query web app (Foundry chat + Cosmos conversation persistence).
- runtime/: ingestion runtime and shared Python logic.
- tests/unit/: fast local tests.
- tests/integration/: private-network smoke and integration tests.

## Required Read Before Major Changes

- docs/implementation-plan.md
- docs/testing-strategy.md
- infra/terraform/README.md
- README.md

## Safe Working Pattern

1. Inspect current diffs first; do not revert unrelated local changes.
2. For infrastructure changes:
   - Validate formatting and config before apply.
   - Prefer targeted apply only for recovery/debug scenarios.
3. For app changes:
   - Run focused unit tests for touched areas.
   - Keep error responses explicit for integration diagnostics.
4. Summarise exactly what changed and what was verified.

## Terraform Guidance

- Prefer cloud-specific working dirs:
  - infra/terraform/azure/
  - infra/terraform/aws/
- Azure typical dev inputs:
  - environments/dev/bootstrap.generated.tfvars
  - environments/dev/dev.tfvars
- Azure validate sequence:
  - terraform -chdir=infra/terraform/azure fmt -recursive modules main.tf
  - terraform -chdir=infra/terraform/azure validate
  - terraform -chdir=infra/terraform/azure plan (preferred)
- AWS validate sequence:
  - terraform -chdir=infra/terraform/aws fmt -recursive modules main.tf
  - terraform -chdir=infra/terraform/aws validate
  - terraform -chdir=infra/terraform/aws plan (preferred)
- Avoid routine use of -target; use only when recovering from failed applies or isolating a known issue.

## Container Rollout Guidance

- Build/push query web image via ops/scripts/azure/build-push-query-web.sh from a Docker-capable host.
- If local Docker is unavailable, use the jumpbox workflow.
- Roll out image tag with Terraform variable query_web_image_tag.
- Use immutable tags formatted as timestamp-hash (or equivalent).

## Python and Test Guidance

- Prefer runtime/.venv when available.
- Run targeted tests first, then broader suites when risk is medium/high.
- Integration tests are expected to run from private network context (for example jumpbox):
  - ops/scripts/azure/run-query-web-integration-tests.sh

## Query Web and Cosmos Guardrails

- Conversation persistence depends on:
  - AZURE_COSMOS_ENDPOINT
  - AZURE_COSMOS_DATABASE_NAME
  - AZURE_COSMOS_CONTAINER_NAME
- Preserve user-scoped conversation access patterns.
- Keep document IDs Cosmos-safe (no illegal characters).
- Surface persistence failures clearly in API responses during diagnostics.

## Azure Identity and Access Guardrails

- Prefer data-plane RBAC with managed identity over account keys.
- Do not introduce long-lived secrets if MI can be used.
- Ensure role assignment scope matches target resource granularity.

## Documentation and Change Hygiene

- Update docs when behaviour, commands, or required env vars change.
- Avoid creating ad hoc markdown summary files unless explicitly requested.
- Keep README and testing instructions aligned with actual scripts and deployment flow.
- Keep Terraform variable availability aligned across:
  - `infra/terraform/azure/terraform.tfvars.example`
  - `infra/terraform/azure/environments/dev/dev.tfvars`
  - `infra/terraform/azure/environments/test/test.tfvars`
  - `infra/terraform/azure/environments/prod/prod.tfvars`
  Add new variables as comments/defaults where appropriate so operators can discover available knobs consistently.

## Definition of Done

A change is complete when all are true:

- Code and Terraform are formatted/valid.
- Relevant unit/integration checks have been run or explicitly called out as not run.
- Deployment steps (if needed) are clear and reproducible.
- No regression to private networking, identity, or least-privilege posture.
