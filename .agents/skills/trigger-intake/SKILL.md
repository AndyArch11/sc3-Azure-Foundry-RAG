---
name: trigger-intake
description: Normalise mention, tag, email notification, and manual request events into a validated assessment job. Use when starting an assessment workflow.
---

# Trigger Intake

## When to use this skill
Use this skill when a new assessment trigger arrives from provider events, email notifications, or direct user requests.

## Inputs
- `trigger_payload` (object, required)
- `source_type` (required): `provider_event`, `email_notification`, or `manual_request`

## Outputs
- `assessment_job` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Validate the incoming payload shape for the declared source type.
2. Classify trigger type (`mention`, `tag`, `email_notification`, `user_request`).
3. Extract provider and target reference.
4. Resolve requester or mentioner identity context.
5. Determine requested identity mode from trigger class and policy.
6. Build a normalised assessment job object.
7. Generate or preserve a stable correlation key for repeat notifications.

## Failure modes
- `unsupported_trigger`
- `malformed_payload`
- `missing_target_reference`
- `missing_request_identity`

## Idempotency rule
Must generate or preserve a stable correlation key for repeated notifications of the same target event.

## Audit fields
- `correlation_id`
- `trigger_type`
- `provider`
- `requester_id`
- `request_identity_mode`

## Guardrails
- Never infer privileged identity context when missing.
- Never silently convert delegated user requests into app-only execution.
- Keep output schema-compatible with shared assessment job contracts.
