# Contract Pack

This directory contains the first contract set for the assessment-agent design.

## Files

- .agents/skills/
  - Canonical standards-based skill pack using one `SKILL.md` per skill.
- shared-schemas.yaml
  - Shared schema definitions for normalised job, target, access, artifact, grounding, and delivery objects.
- provider-event-contracts.yaml
  - Index of provider event contract files.
- provider-events-sharepoint.yaml
  - Initial normalised event contract for SharePoint / Microsoft 365 assessment triggers.
- provider-events-confluence.yaml
  - Initial normalised event contract for Confluence assessment triggers.
- provider-events-email.yaml
  - Initial normalised event contract for email-trigger intake.
- orchestrator-queue-message.yaml
  - Queue hand-off contract between intake and orchestrated execution.
- mcp-sharepoint-tools.yaml
  - Initial MCP tool contract set for SharePoint / Microsoft 365 content operations.
- mcp-confluence-tools.yaml
  - Initial MCP tool contract set for Confluence content operations.
- mcp-email-tools.yaml
  - Initial MCP tool contract set for email-trigger intake and email delivery.

## Intended Use

These contracts are design-time interface documents.

The canonical skill source of truth is `.agents/skills/`.

They are meant to:

- guide implementation boundaries
- support future code generation or schema validation
- make provider and skill responsibilities explicit
- reduce ambiguity before runtime implementation begins

## Notes

These are initial contracts rather than final provider-complete schemas.

Expected future work:

- add shared schema definitions for common objects
- align naming with any eventual MCP server implementation framework
- version tool contracts independently if provider behaviour diverges
- align runtime scaffold modules in `runtime/assessment_orchestration/` with these contracts as implementation matures
