---
name: content-retrieval
description: Retrieve normalised target content and metadata through MCP servers. Use when assessment input content must be assembled.
---

# Content Retrieval

## When to use this skill
Use this skill after target resolution and access validation to fetch the assessable artifact package.

## Inputs
- `resolved_target` (required)
- `identity_mode` (required): `app_only` or `delegated`
- `include_discussion_context` (optional boolean)

## Outputs
- `assessed_artifact_package` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Request provider content and metadata through MCP server tools.
2. Retrieve body, title, canonical URL, owner, and last editor.
3. Optionally retrieve adjacent discussion context.
4. Normalise response into the shared assessed artifact package shape.
5. Include content version and retrieval timestamp metadata.

## Failure modes
- `target_not_found`
- `content_not_readable`
- `provider_timeout`
- `permissions_denied`

## Idempotency rule
Retrieval must be side-effect free.

## Audit fields
- `provider`
- `target_id`
- `content_version`
- `retrieved_at`

## Guardrails
- Do not proceed with partial metadata when required fields are missing.
- Preserve canonical source links for downstream citation.
- Keep provider-specific fields in metadata, not top-level schema fields.
