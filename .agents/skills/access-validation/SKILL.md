---
name: access-validation
description: Enforce delegated versus app-only access constraints for each operation. Use when selecting or verifying identity mode.
---

# Access Validation

## When to use this skill
Use this skill before any provider read or write operation.

## Inputs
- `request_context` (object, required)
- `resolved_target` (required)
- `requested_operation` (required): `read_content`, `publish_comment`, or `send_email`

## Outputs
- `access_decision` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Verify request context includes actor and requested identity mode.
2. Apply policy for trigger class and operation.
3. Require delegated mode for user-initiated requests.
4. Reject silent privilege escalation attempts.
5. Produce explicit grant or deny decision with reason.

## Failure modes
- `delegated_required`
- `insufficient_permissions`
- `silent_privilege_escalation_attempt`
- `identity_context_missing`

## Idempotency rule
Validation must be side-effect free.

## Audit fields
- `request_identity_mode`
- `resolved_identity_mode`
- `access_granted`
- `access_denial_reason`

## Guardrails
- Never downgrade delegated requests to app-only.
- Explicitly record denial reason for governance and debugging.
- Do not permit write actions without operation-specific access checks.
