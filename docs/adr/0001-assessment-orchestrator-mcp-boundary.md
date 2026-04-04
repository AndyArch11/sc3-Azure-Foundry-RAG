# ADR 0001: Assessment Orchestrator, Assessment Agent, and MCP Server Ownership Boundary

## Status

Accepted

## Context

The assessment design introduces three logical execution domains:

- assessment orchestrator
- assessment agent
- provider MCP servers

Without a formal boundary, implementation would drift toward one of two failure modes:

1. The orchestrator accumulates provider-specific logic, permission handling, and publication behaviour, which weakens the MCP abstraction.
2. MCP servers accumulate compliance-specific retrieval and assessment behaviour, which duplicates policy and reasoning logic per provider.

The system also has two access modes:

- app-only for system-triggered requests
- delegated for user-initiated requests

That makes ownership boundaries security-relevant, not just an implementation preference.

## Decision

Ownership is split as follows.

### Assessment Orchestrator Owns

- job intake and scheduling
- trigger normalisation
- access-mode policy enforcement
- delivery policy evaluation
- idempotency and deduplication
- retries, timeout handling, and escalation
- audit and trace persistence
- coordination of corpus retrieval and assessment execution

### Assessment Agent Owns

- corpus retrieval against Corpus A and Corpus B
- compliance reasoning over the assessed artifact and grounding package
- structured report generation
- schema-aware findings generation

### MCP Servers Own

- provider authentication and token refresh
- delegated versus app-only execution enforcement at provider boundary
- provider object lookup and normalisation
- target content retrieval and metadata retrieval
- owner and last-editor resolution
- comment publication and email send/read operations

### MCP Servers Do Not Own

- compliance retrieval policy
- compliance reasoning
- findings schema policy
- delivery policy selection
- cross-provider orchestration logic

## Rationale

This split preserves a clean separation of concerns.

- The orchestrator remains the policy and workflow control plane.
- The assessment agent remains the reasoning component.
- MCP servers remain provider adapters with strong security enforcement.

This gives the best outcome for:

- least privilege
- consistent policy application
- testability
- provider portability
- auditability

## Security Consequences

- Delegated requests must never silently downgrade to app-only execution.
- MCP servers must validate the requested identity mode per call.
- User-requested assessment must use delegated mode.
- System-triggered assessment may use app-only mode, but only within approved provider scopes.
- Public ingress for webhook intake, if introduced, must be separate from the private orchestrator.

## Operational Consequences

Positive:

- provider logic is isolated
- assessment policy is centralised
- skill ownership is explicit
- testing can be split by layer

Negative:

- more interfaces must be specified up front
- orchestrator and MCP servers need a normalised contract layer
- there is some duplication of metadata shapes across providers until shared schemas are introduced

## Confluence Authentication Rollout Note (April 2026)

- Accepted near-term approach:
  - Use service-account app-only authentication for Confluence assessment workflows.
  - Keep this compatible with private-network deployment patterns.
- Deferred approach:
  - Delegated OAuth 3LO for user-context execution is postponed.
  - Reason: tenant-specific app registration, consent overhead, and callback/public ingress requirements complicate secure multi-client rollout.
- Future condition for revisit:
  - Revisit delegated OAuth when a dedicated public auth broker and tenant onboarding model are prioritised.

## Alternatives Considered

### Alternative 1: Thin MCP Servers

Description:

- MCP servers only manage connections and raw provider transport.
- Orchestrator owns provider-specific resource lookup and publication behaviour.

Rejected because:

- provider logic leaks into orchestrator
- delegated/app-only enforcement becomes inconsistent
- provider portability worsens

### Alternative 2: Fat MCP Servers

Description:

- MCP servers also perform corpus retrieval, compliance assessment, and response decisions.

Rejected because:

- reasoning policy becomes duplicated by provider
- schema and validation logic fragment
- audit and retry semantics become inconsistent

## Implementation Notes

This ADR is implemented through three document sets:

- orchestration design: docs/agent-assessment-orchestration.md
- standards-based skills (canonical): .agents/skills/*/SKILL.md
- MCP tool contracts:
  - docs/contracts/mcp-sharepoint-tools.yaml
  - docs/contracts/mcp-confluence-tools.yaml
  - docs/contracts/mcp-email-tools.yaml

## Review Trigger

Review this ADR if any of the following become true:

- MCP servers start owning compliance logic
- orchestrator starts owning provider-specific content parsing logic
- delegated and app-only modes require a different enforcement model
- a multi-agent runtime replaces the current orchestrator plus assessment-agent model
