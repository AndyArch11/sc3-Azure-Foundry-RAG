---
name: corpus-retrieval
description: Retrieve grounded controls and guidance from Corpus A and Corpus B. Use when preparing evidence for compliance assessment.
---

# Corpus Retrieval

## When to use this skill
Use this skill after content retrieval and before assessment generation.

## Inputs
- `assessed_artifact_package` (required)

## Outputs
- `corpus_grounding_package` (required)

## Allowed identity modes
- `app_only`
- `delegated`

## Procedure
1. Build retrieval query from target content and metadata.
2. Retrieve authoritative controls from Corpus A.
3. Retrieve interpretive guidance from Corpus B.
4. Apply framework and precedence policy constraints.
5. Return a grounding package with provenance metadata.

## Failure modes
- `corpus_a_unavailable`
- `corpus_b_unavailable`
- `no_relevant_controls`
- `retrieval_timeout`

## Idempotency rule
Retrieval should be side-effect free for identical inputs.

## Audit fields
- `controls_retrieved_count`
- `guidance_retrieved_count`
- `retrieval_policy_version`

## Guardrails
- Treat Corpus A as authoritative requirements.
- Treat Corpus B as interpretive context only.
- Preserve source citations for each retrieved element.
