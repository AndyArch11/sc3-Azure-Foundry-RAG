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

## Cosmos Schema Evolution Strategy (Rolling Changes)

When Cosmos-backed documents evolve (for example conversation history, polling state, or assessment snapshots), use a compatibility-first strategy so old and new application versions can run concurrently during deployment.

### Design Principles

- Always include a per-document schema marker (for example `schema_version`) in each logical document type.
- Prefer additive changes first: add new optional fields before removing or renaming old fields.
- Keep backward compatibility in read paths for at least one full deployment window.
- Separate API contract versioning from storage schema versioning. They can move independently.
- Use deterministic IDs/partition keys that do not change across schema versions.

### Recommended Rollout Pattern

1. Expand readers first
  - Update all readers to support both current (`vN`) and next (`vN+1`) document shapes.
  - Implement a small upcaster/normaliser layer that converts raw Cosmos payloads into the in-memory canonical model.

2. Enable dual-write (optional but recommended for breaking changes)
  - During transition, write both old and new fields (or old and new document envelopes) from the same transaction boundary.
  - Keep writes idempotent (`upsert`) and preserve `_etag` checks where optimistic concurrency is used.

3. Switch default writes to new schema
  - Once all live clients can read `vN+1`, make `vN+1` the default write format.
  - Continue accepting `vN` reads until migration completion criteria are met.

4. Run backfill migration
  - Use a resumable, batched migration job keyed by partition ranges and continuation tokens.
  - Record migration progress in a dedicated control document collection or migration ledger.
  - Include dry-run mode and metrics (`scanned`, `updated`, `failed`, `skipped`).

5. Contract cleanup
  - Remove legacy read/write branches only after migration SLO is met (for example 99.9 percent upgraded + no old writers).
  - Announce deprecation window and enforce via runtime guardrails.

### Client Impact Management

- Compatibility matrix
  - Document which app version can read/write each schema version (`vN`, `vN+1`).
  - Block deployment if a new writer would produce unreadable documents for still-running readers.

- Feature flags
  - Gate schema-changing write behaviour behind flags to support quick rollback.

- Validation
  - Validate on write and on read-normalisation; route invalid docs to quarantine diagnostics instead of hard-failing entire workflows.

- Observability
  - Emit schema counters (documents read by version, write version, upcast path hit rate, migration lag, migration errors).

### Document-Type Specific Guidance

Conversation history documents:
- Keep message arrays append-only where possible.
- For field renames, keep old field as alias during transition (`user_id` and `principal_id`, etc.).
- If message object shape changes, version at message level only when needed; otherwise version at document root.

Polling state documents:
- Preserve critical lease/lock semantics and watermark fields during migration.
- Never migrate lock documents in-place while active lease ownership is unknown; let lock docs expire naturally or rotate safely.
- Keep state transition fields (`last_processed_event_id`, retry counters, error records) backward readable.

Assessment snapshot documents:
- Keep snapshot identity stable (`source`, `target_id`, `framework_scope`).
- For new assessment metadata, add nullable fields first and backfill asynchronously.
- Ensure dedupe keys and content hash semantics are unchanged across schema versions unless explicitly re-baselined.

### Data Migration Safety Checklist

- Backups or point-in-time restore readiness confirmed.
- Migration idempotency verified (safe to retry same batch).
- `_etag` conflict strategy defined (retry with bounded backoff).
- Throughput controls in place to avoid RU starvation of online traffic.
- Canary migration validated on a small partition subset.
- Rollback plan documented (reader fallback + writer flag rollback).

### Minimal Metadata to Add to Cosmos Documents

- `schema_version` (storage schema)
- `created_at`, `updated_at`
- `migrated_at` (optional)
- `migration_run_id` (optional)

This metadata enables safe rolling upgrades, faster incident triage, and deterministic migration reporting.

### Example Migration Runbook Template

Use this template for each schema migration. Store completed runbooks alongside the migration ledger.

```yaml
migration_id: cosmos-conversation-v1-to-v2-2026-05
schema_type: conversation_history           # conversation_history | polling_state | assessment_snapshot
from_version: "v1"
to_version:   "v2"
target_container: conversations
partition_key_path: /session_id

change_summary: |
  Rename user_id -> principal_id.
  Add optional mfa_verified (bool, default null).
  Remove deprecated legacy_source field (present in <3 % of docs).

estimated_docs: 180000
backfill_batch_size: 250
max_ru_per_second: 1000                     # Stay well below provisioned RU to protect live traffic

phases:
  - phase: expand-readers
    gate: all services deployed at >=v2.4.0 before proceeding
  - phase: dual-write
    duration: 48h minimum canary window
    gate: error rate <0.1 %, schema_version_written metric stable at v2
  - phase: backfill
    command: python ops/scripts/migrate_cosmos.py --run-id cosmos-conversation-v1-to-v2-2026-05 --dry-run
    checkpoints: every 10000 docs, checkpoint doc written to migration_ledger container
    gate: scanned == updated + skipped, failed == 0
  - phase: cleanup
    gate: schema_version_read{version="v1"} == 0 for 72h (see Activity Logging below)
    action: remove v1 read branches, remove legacy_source field handling

rollback_procedure: |
  1. Re-deploy previous service version (reader falls back to v1 shape automatically via upcaster).
  2. Flip feature flag `COSMOS_WRITE_SCHEMA_VERSION=v1` to revert write path.
  3. No document rollback required unless dual-write was disabled prematurely.

contacts:
  owner: platform-team
  approver: tech-lead
  last_updated: 2026-05-01
```

### Activity Logging for Schema Version Monitoring

**Why this matters:** You cannot safely decommission backward-compatibility code for schema `vN` until you have evidence that no live client is still reading or writing `vN` documents. Activity logging is the prerequisite for that evidence.

Every Cosmos read and write path **must** emit a structured log line that includes:

| Field | Description | Example |
|---|---|---|
| `schema_version_read` | Schema version found on the retrieved document | `"v1"` |
| `schema_version_written` | Schema version stamped on the written document | `"v2"` |
| `upcasted` | Whether the upcaster ran on this read | `true` |
| `client_id` | Service identity of the caller (see below) | `"query-web"` |
| `operation` | `read` / `write` / `upsert` | `"read"` |
| `container` | Cosmos container name | `"conversations"` |
| `correlation_id` | Request/job trace ID | `"abc-123"` |

Example Python log call:

```python
logger.info(
    "cosmos_schema_access",
    extra={
        "schema_version_read": doc.get("schema_version", "unknown"),
        "schema_version_written": target_version,
        "upcasted": upcasted,
        "client_id": settings.SERVICE_NAME,   # e.g. "query-web", "ingestion-worker"
        "operation": "upsert",
        "container": container_name,
        "correlation_id": ctx.correlation_id,
    },
)
```

Recommended monitoring rules (Azure Monitor / Log Analytics):

```kusto
// Alert: any client still reading v1 documents after cleanup gate
AppTraces
| where Message == "cosmos_schema_access"
| extend schema_version_read = tostring(Properties["schema_version_read"])
| where schema_version_read == "v1"
| summarize count() by bin(TimeGenerated, 1h), client_id = tostring(Properties["client_id"])
| where count_ > 0
```

- Create a **dashboard tile** per deprecated schema version showing read/write counts over time. A 72-hour zero-count window on `schema_version_read == "vN"` is the cleanup gate signal.
- Create an **alert** that fires if `schema_version_read == "vN"` count rises after the cleanup gate has been passed (regression detection).

### Identifying Dependent Clients Before Decommissioning

The `client_id` / `service_name` field in every log line enables you to surface *which* services are still coupled to a deprecated schema. Without it, you know *that* old reads are happening but not *who* to contact.

Identification query (run before any cleanup phase):

```kusto
AppTraces
| where Message == "cosmos_schema_access"
| where TimeGenerated > ago(7d)
| extend schema_version_read = tostring(Properties["schema_version_read"]),
         client_id            = tostring(Properties["client_id"])
| where schema_version_read == "v1"
| summarize last_seen = max(TimeGenerated), access_count = count()
            by client_id, schema_version_read
| order by last_seen desc
```

Use the output as the **dependent-client list** in your runbook's cleanup gate. For each client still appearing:

1. Link the `client_id` to its owning team in your service catalogue.
2. Raise a migration ticket referencing the runbook `migration_id`.
3. Block the cleanup phase until the client has shipped and the query above returns no rows for that `client_id` over the monitoring window.

This creates a traceable, evidence-based decommissioning trail and prevents silent breakage of services that were overlooked during the initial migration rollout.

Operational safety notes:

- Corpus clear APIs support dry-run previews before deletion:
  - `POST /api/corpus-a/clear`
  - `POST /api/corpus-b/clear`
  - `POST /api/corpus-c/clear`
- Use `dry_run=true` first to verify impact (`would_delete` counters), then execute with `dry_run=false`.
- Corpus B (grounding) and Corpus C (evidence) clear operations can optionally remove source blobs with `clear_blobs=true`.

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