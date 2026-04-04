---
name: assessment
description: Produce a structured compliance assessment report from retrieved content and corpus grounding. Use when generating findings and citations.
---

# Assessment

## When to use this skill
Use this skill when assessed artifact and corpus grounding packages are available and a structured report is required.

## Inputs
- `assessed_artifact_package` (required)
- `corpus_grounding_package` (required)

## Outputs
- `structured_assessment_report` (required)
- `report_markdown` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Validate required input schemas and required fields.
2. Compare target artifact evidence against Corpus A requirements.
3. Use Corpus B as interpretive context only.
4. Generate structured findings with citations and explicit evidence status.
5. Render report markdown for delivery channels.
6. Validate output schema before returning.

## Failure modes
- `schema_validation_failed`
- `assessment_timeout`
- `insufficient_evidence`
- `model_execution_failed`

## Idempotency rule
Results may vary with model configuration; persist model and schema version for replayability.

## Audit fields
- `schema_version`
- `validation_mode`
- `model_deployment`
- `corpus_a_basis_count`
- `corpus_b_basis_count`

## Guardrails
- Never emit uncited assertions as compliance findings.
- Mark missing evidence explicitly.
- Preserve schema and model metadata for audit replay.
