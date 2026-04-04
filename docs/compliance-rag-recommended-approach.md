# Compliance RAG Recommended Approach

## Goal
Build a capability that can:
1. Retrieve authoritative requirements from standards (NIST, Essential Eight, internal standards).
2. Retrieve supporting narrative guidance from standards, guidance packs, and other less-structured authoritative material.
3. Retrieve assessed artifacts such as designs, policies, procedures, architecture descriptions, and enterprise evidence from Confluence, SharePoint, and Office documents.
4. Assess potential compliance gaps by comparing requirements and guidance against assessed artifacts.
5. Return traceable, citation-backed findings with confidence.

## Recommended Architecture

Use a multi-corpus model instead of a single generic ingestion path.

### Corpus A: Normative Requirements (authoritative controls)
- Source examples: NIST publications, Essential Eight guidance, internal standard documents.
- Ingestion method: pre-parse and normalise into one record per requirement/control.
- Why: requirement-level granularity is needed for deterministic control mapping, filtering, comparison, and auditability.

### Corpus B: Narrative Guidance (authoritative but weakly structured)
- Source examples: narrative standards, implementation guidance, explanatory control commentary, handbooks, better-practice guidance, internal guidance material.
- Ingestion method: generic extraction/chunking/indexing pipeline, optionally with lightweight metadata enrichment.
- Why: some authoritative source material is useful as grounding but does not lend itself to reliable atomic requirement parsing.

### Corpus C: Assessed Artifacts (designs, evidence, and implementation material)
- Source examples: Confluence pages, SharePoint files, policy/procedure docs, design docs, runbooks, build standards, architecture descriptions, operating models, implementation evidence.
- Ingestion method: existing generic extraction/chunking/indexing pipeline.
- Why: assessed content is heterogeneous and benefits from broad full-text + vector retrieval.

### Corpus D: Authority and Precedence Policy (conflict resolution)
- Source examples: internal governance policy, regulator guidance precedence statements, enterprise policy hierarchy, jurisdiction-specific override rules.
- Ingestion method: structured policy records (small, explicit, versioned) with effective dates and scope.
- Why: when two standards conflict or diverge, the system needs deterministic preference rules rather than ad hoc model judgement.

### Query Pattern
1. Retrieve requirement candidates from Corpus A.
2. Retrieve interpretive guidance candidates from Corpus B.
3. Retrieve assessed-artifact candidates from Corpus C.
4. Apply precedence policy from Corpus D when discrepancies or contradictions exist.
5. Perform LLM-grounded comparison and produce structured assessment output.
6. Require citations for all factual claims.

## Why This Approach

The current pipeline is strong for generic RAG, but it does not reliably represent one requirement as one canonical unit with rich metadata. For compliance assessments, pre-parsed requirement records provide:
- Better precision for control-level retrieval.
- Better filtering/faceting by framework, version, control family, and applicability.
- Better explainability and audit trail.
- Lower risk of false positives/false negatives in gap analysis.

At the same time, not all authoritative material is naturally atomic. Narrative standards and guidance can still be used as grounding data, but they should be treated as interpretive context rather than canonical requirement records unless they can be reliably normalised.

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

## Authority Model

Treat the corpora differently during retrieval and assessment:

- Corpus A is obligation-bearing. Use it for canonical requirement IDs, structured gap findings, and pass/partial/missing decisions.
- Corpus B is interpretive. Use it to refine meaning, provide implementation context, and reduce ambiguity, but do not treat it as equal to Corpus A for obligation logic unless it has been normalised into canonical requirement records.
- Corpus C is the assessed target. Use it as the source of claimed design intent, implementation detail, and operational evidence.
- Corpus D defines precedence. Use it when sources conflict, and record which rule was applied.

This distinction is important. Without it, the system may over-weight descriptive or advisory narrative text as if it were a mandatory requirement.

## Prompt and Response Contract

Prompt design guidance:

- Explicitly label retrieved context by corpus (A/B/C) and include active precedence policy (D).
- Instruct the model to treat Corpus A as obligation-bearing and Corpus B as interpretive.
- Require explicit contradiction handling: identify conflict, apply precedence rule, and explain rationale.

Response contract guidance:

- `decision` (short conclusion)
- `corpus_a_basis` (requirement IDs, framework/version, normative rationale)
- `corpus_b_basis` (guidance context or explicit "not available")
- `corpus_c_basis` (assessed artifacts/evidence and relevance)
- `precedence_resolution` (policy applied, winning source, impact)
- `gaps_and_actions` (missing controls, remediation priorities)
- `citations` (requirement IDs + source references)
- `confidence` (score and what would raise confidence)

For machine-readable APIs, expose the same fields structurally in addition to markdown text.

Current implementation notes:

- `POST /api/compliance/report` enforces a versioned structured schema with `schema_version="v1.1"`.
- `validation_mode` supports `hard` (fail request on schema mismatch) and `soft` (return raw output plus validation error details).
- API responses include markdown and machine-consumable artifacts (`report_markdown`, `report_structured`, `report_findings_csv`).

Operational safety notes:

- Corpus clear APIs support dry-run previews before deletion:
  - `POST /api/corpus-a/clear`
  - `POST /api/corpus-b/clear`
  - `POST /api/corpus-c/clear`
- Use `dry_run=true` first to verify impact (`would_delete` counters), then execute with `dry_run=false`.
- Corpus B/C clear can optionally remove source blobs with `clear_blobs=true`.

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
3. Keep current blob skillset ingestion for narrative guidance and assessed-artifact corpora, with metadata tags that distinguish their roles.
4. Enable semantic ranking on the service and at query time.

Deliverables:
- Controls index schema and indexer/loader
- Guidance/evidence index schema retained or refined
- Semantic configurations documented per index

## Phase 3: Retrieval and Comparison Engine
Actions:
1. Implement staged retrieval across requirements, guidance, and assessed artifacts.
2. Add structured comparison prompt template.
3. Produce machine-readable output fields:
   - `requirement_id`
   - `status` (covered, partial, missing, unknown)
  - `assessment_target`
   - `evidence_summary`
  - `guidance_summary`
  - `precedence_rule_applied`
  - `precedence_decision_reason`
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
4. Tag unstructured content by role so narrative guidance and assessed artifacts are distinguishable at retrieval time.
5. Define and version the precedence policy corpus for contradiction handling.
6. Implement multi-corpus retrieval in query service and compare results against current single-index behaviour.
7. Define evaluation rubric and run a first benchmark before broader rollout.

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
- Pre-parsed requirement records for standards and control frameworks where atomic parsing is reliable.
- Generic ingestion for narrative authoritative guidance that is still valuable as grounding but not yet canonical.
- Generic ingestion for assessed artifacts and enterprise evidence.
- A small, explicit precedence-policy corpus to resolve cross-standard discrepancies deterministically.
- Multi-corpus retrieval and structured comparison for compliance assessment and design-review workflows.

This provides the best balance of precision, explainability, scalability, and practical support for design-gap analysis against both mandatory requirements and supporting guidance.