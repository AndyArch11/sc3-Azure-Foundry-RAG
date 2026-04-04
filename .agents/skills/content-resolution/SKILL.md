---
name: content-resolution
description: Resolve provider URLs and object references into canonical targets before retrieval. Use when a job has only a raw target reference.
---

# Content Resolution

## When to use this skill
Use this skill before content retrieval when the workflow has a URL, ID, or provider-specific reference that must be normalised.

## Inputs
- `provider` (required): `confluence`, `sharepoint`, or `m365`
- `target_reference` (object, required)

## Outputs
- `resolved_target` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Validate provider and target reference shape.
2. Normalise URL and ID representations.
3. Route request to the correct provider MCP server.
4. Resolve canonical object metadata.
5. Return provider, target ID, canonical URL, and title.

## Failure modes
- `unsupported_provider`
- `invalid_url`
- `target_not_found`
- `ambiguous_reference`

## Idempotency rule
Resolution must be side-effect free.

## Audit fields
- `provider`
- `target_id`
- `canonical_url`

## Guardrails
- Do not continue if target type is unsupported.
- Do not mutate provider content during resolution.
- Ensure canonical URL is stable for downstream deduplication.
