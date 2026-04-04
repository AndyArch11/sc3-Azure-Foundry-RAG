---
name: audit-and-trace
description: Persist stage-level audit and trace data for full assessment reconstruction. Use throughout orchestration to maintain governance evidence.
---

# Audit And Trace

## When to use this skill
Use this skill at each orchestration stage transition and on terminal job outcomes.

## Inputs
- `stage_outputs` (required)

## Outputs
- `audit_record` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Verify correlation and job identifiers are present.
2. Capture stage output metadata and decision context.
3. Persist provider, target, actor, identity mode, and delivery decision fields.
4. Write append-safe or upsert-safe records keyed by correlation and stage.
5. Return stored audit record identifiers and status.

## Failure modes
- `audit_store_unavailable`
- `correlation_missing`
- `trace_write_failed`

## Idempotency rule
Audit writes must be append-safe or upsert-safe by correlation and stage.

## Audit fields
- `correlation_id`
- `job_id`
- `provider`
- `target_id`
- `request_identity_mode`
- `delivery_policy`
- `final_status`

## Guardrails
- Do not suppress audit write failures.
- Ensure every terminal outcome has an audit record.
- Keep audit data reconstructable without provider-specific parsing.
