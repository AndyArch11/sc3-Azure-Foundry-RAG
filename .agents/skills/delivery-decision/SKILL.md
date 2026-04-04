---
name: delivery-decision
description: Select assessment delivery channels using policy and provider capability checks. Use when planning inline comments, email, or fallback behaviour.
---

# Delivery Decision

## When to use this skill
Use this skill after assessment generation and before publication.

## Inputs
- `structured_assessment_report` (required)
- `job_metadata` (required)
- `provider_capabilities` (required)

## Outputs
- `delivery_plan` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Load delivery policy for provider and workspace.
2. Evaluate inline publication capability and permission outcome.
3. Resolve recipient list using recipient selection policy order.
4. Select channels: inline, email, both, or inline fallback to email.
5. Return deterministic delivery plan with selected channels and targets.

## Failure modes
- `no_valid_delivery_target`
- `restricted_inline_delivery`
- `restricted_email_delivery`

## Idempotency rule
Delivery planning must be side-effect free.

## Audit fields
- `delivery_policy`
- `inline_selected`
- `email_selected`
- `recipients`

## Guardrails
- Do not guess broad distribution recipients.
- Record reason for each selected recipient.
- Enforce confidentiality and provider policy constraints.
