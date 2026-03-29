# Compliance RAG Recommended Approach

## Goal
Build a capability that can:
1. Retrieve authoritative requirements from standards (NIST, Essential Eight, internal standards).
2. Retrieve enterprise evidence from Confluence, SharePoint, and Office documents.
3. Assess potential compliance gaps by comparing requirements vs evidence.
4. Return traceable, citation-backed findings with confidence.

## Recommended Architecture

Use a dual-corpus model instead of a single generic ingestion path.

### Corpus A: Normative Requirements (authoritative controls)
- Source examples: NIST publications, Essential Eight guidance, internal standard documents.
- Ingestion method: pre-parse and normalise into one record per requirement/control.
- Why: requirement-level granularity is needed for deterministic control mapping and auditability.

### Corpus B: Enterprise Evidence (implementation artifacts)
- Source examples: Confluence pages, SharePoint files, policy/procedure docs, design docs, runbooks.
- Ingestion method: existing generic extraction/chunking/indexing pipeline.
- Why: evidence content is heterogeneous and benefits from broad full-text + vector retrieval.

### Query Pattern
1. Retrieve requirement candidates from Corpus A.
2. Retrieve evidence candidates from Corpus B.
3. Perform LLM-grounded comparison and produce structured assessment output.
4. Require citations for all factual claims.

## Why This Approach

The current pipeline is strong for generic RAG, but it does not reliably represent one requirement as one canonical unit with rich metadata. For compliance assessments, pre-parsed requirement records provide:
- Better precision for control-level retrieval.
- Better filtering/faceting by framework, version, control family, and applicability.
- Better explainability and audit trail.
- Lower risk of false positives/false negatives in gap analysis.

## Target Requirement Record Model

Create a normalised record for each requirement/control statement.

Required fields:
- `requirement_id` (stable canonical id)
- `framework` (for example: NIST, Essential Eight, Internal)
- `framework_version`
- `control_family`
- `control_title`
- `requirement_text` (normative statement)
- `guidance_text` (optional supplemental guidance)
- `keywords` (optional controlled tags)
- `source_uri`
- `source_section`
- `effective_date` (optional)
- `jurisdiction_or_scope` (optional)

Recommended search index attributes:
- Searchable: `control_title`, `requirement_text`, `guidance_text`
- Filterable/facetable: `framework`, `framework_version`, `control_family`, `jurisdiction_or_scope`
- Key field: `requirement_id`
- Vector field: embedding of requirement text (and optionally title)

## Semantic Ranking Configuration Guidance

Define at least one semantic configuration for requirements:
- Title field: `control_title`
- Content fields: `requirement_text`, `guidance_text`
- Keyword fields: `keywords` (only if they are curated and meaningful)

Define a separate semantic configuration for evidence index if needed:
- Title field: document/page title (or filename)
- Content fields: extracted content
- Keyword fields: document taxonomy tags

Use multiple semantic configurations only when query intent differs materially (for example control lookup vs gap analysis narrative).

## Suggested Build Plan

## Phase 1: Data Contract and Parsing
Actions:
1. Define a versioned JSON schema for requirement records.
2. Build a parser/normaliser for each framework source format.
3. Add validation rules for mandatory fields and id uniqueness.
4. Produce normalised requirement artifacts (JSONL recommended).

Deliverables:
- `requirements-schema.json`
- `normalised-controls/*.jsonl`
- Parsing validation report (missing fields, duplicate IDs, parse confidence)

## Phase 2: Index Design and Ingestion
Actions:
1. Create a dedicated controls index in Azure AI Search.
2. Extend ingestion to load normalised requirement records directly.
3. Keep current blob skillset ingestion for evidence corpus.
4. Enable semantic ranking on the service and at query time.

Deliverables:
- Controls index schema and indexer/loader
- Evidence index schema retained or refined
- Semantic configurations documented per index

## Phase 3: Retrieval and Comparison Engine
Actions:
1. Implement two-stage retrieval (requirements + evidence).
2. Add structured comparison prompt template.
3. Produce machine-readable output fields:
   - `requirement_id`
   - `status` (covered, partial, missing, unknown)
   - `evidence_summary`
   - `gaps`
   - `recommended_actions`
   - `citations`
   - `confidence`
4. Add thresholding and fallback behaviour when evidence is weak.

Deliverables:
- Compliance assessment response contract
- Prompt and evaluator templates
- End-to-end API for assessment flow

## Phase 4: Quality and Governance
Actions:
1. Build a gold test set for control retrieval and gap assessment.
2. Track metrics:
   - requirement retrieval recall@k
   - citation precision
   - unsupported-claim rate
   - false gap rate
   - missed gap rate
3. Add human review workflow for low-confidence results.
4. Add data/version governance for framework updates.

Deliverables:
- Test harness and baseline metrics
- Acceptance thresholds for release gates
- Operational runbook for index and framework version updates

## Immediate Next Actions (Practical, Low Risk)

1. Add semantic ranking usage in query path for current hybrid search.
2. Add a dedicated controls index with metadata-first schema.
3. Create a small pilot corpus (20-50 controls across NIST + Essential Eight) as normalised records.
4. Implement dual retrieval in query service and compare results against current single-index behavior.
5. Define evaluation rubric and run a first benchmark before broader rollout.

## Risks and Mitigations

- Risk: over-reliance on unstructured chunk retrieval causes weak control mapping.
  - Mitigation: pre-parse authoritative controls into canonical records.

- Risk: framework updates invalidate control references.
  - Mitigation: version fields plus controlled migration process.

- Risk: hallucinated compliance assertions.
  - Mitigation: strict grounding prompts, required citations, evaluator gate, human review for low confidence.

- Risk: noisy enterprise sources reduce precision.
  - Mitigation: metadata filtering, source quality tiers, and confidence thresholding.

## Decision Summary

For this project, use:
- Pre-parsed requirement records for standards and control frameworks.
- Generic skillset ingestion for enterprise evidence documents.
- Dual retrieval and structured comparison for compliance assessment.

This provides the best balance of precision, explainability, and scalability.