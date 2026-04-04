---
name: publication
description: Execute delivery plans through provider MCP servers for comments and email. Use when publishing completed assessment outputs.
---

# Publication

## When to use this skill
Use this skill when a delivery plan has been approved and report artefacts are ready.

## Inputs
- `delivery_plan` (required)
- `report_artifacts` (required)

## Outputs
- `delivery_outcome` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Validate plan channel targets and identity mode.
2. Execute inline comment publication where selected.
3. Execute email send where selected.
4. Apply fallback rules if inline publication fails and policy permits.
5. Return channel-by-channel outcome with failure details.

## Failure modes
- `comment_publish_failed`
- `email_send_failed`
- `provider_timeout`
- `permissions_denied`

## Idempotency rule
Publication must support orchestrator-supplied idempotency keys to avoid duplicate comments or emails.

## Audit fields
- `publication_attempts`
- `comment_published`
- `email_sent`
- `fallback_used`

## Guardrails
- Never publish twice for the same idempotency key.
- Capture provider response identifiers for traceability.
- Return partial-success outcomes explicitly.
