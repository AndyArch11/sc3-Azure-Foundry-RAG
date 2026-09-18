# Relationship Graph Deliverables Tracker

This document tracks deliverables for the Corpus A and Corpus B relationship graph initiative.

Scope:
- Included: Corpus A and Corpus B graph entities and relationships.
- Excluded (current phase): Corpus C graph modeling.

Priority rollout order:
1. Local
2. Azure
3. AWS

## Program Status

- Overall status: In progress
- Current milestone: Milestone 4 (AWS Rollout)
- Last updated: 2026-07-06

## Milestone 1: Graph Foundation

Status: Completed

Deliverables:
- [x] Graph ontology and edge definitions finalised
- [x] Deterministic ID strategy documented (A and B)
- [x] Dedupe key design finalised
- [x] `graph_schema_version` strategy finalised
- [x] Config flags and thresholds defined

Acceptance criteria:
- Repeated runs on identical input produce identical node and edge IDs.
- Ontology explicitly separates structural edges from inferred guidance edges.

## Milestone 2: Batch Graph Build (Local First)

Status: Completed

### Work Items

| ID | Deliverable | Status | Priority | Owner | Notes |
|---|---|---|---|---|---|
| T2-01 | Define node/edge artifact schemas | Completed | P0 | TBD | Implemented in `query_web/graph_artifacts.py` |
| T2-02 | Deterministic node ID builder (A/B) | Completed | P0 | TBD | Implemented for Corpus A/B node records |
| T2-03 | Edge construction and idempotent dedupe | Completed | P0 | TBD | Structural/inferred edge builders + deterministic dedupe implemented |
| T2-04 | Size-aware scanning depth heuristics | Completed | P0 | TBD | Implemented in `query_web/graph_traversal.py` with tests |
| T2-05 | Local batch graph builder artifacts | Completed | P0 | TBD | Implemented in `query_web/graph_batch.py` + tests |
| T2-06 | Local graph persistence baseline | Completed | P1 | TBD | Implemented in `query_web/graph_store.py` + tests |
| T2-07 | Local graph API surface and OpenAPI updates | Completed | P1 | TBD | Endpoints + OpenAPI + AskResponse graph fields implemented |
| T2-08 | Observability and build audit | Completed | P1 | TBD | Graph metrics + endpoint audit payloads implemented |
| T2-09 | Determinism/heuristics/contract tests | Completed | P0 | TBD | Determinism + heuristics + OpenAPI contract checks validated |
| T2-10 | Azure/AWS adapter readiness spike | Completed | P2 | TBD | Backend interface + local/azure/aws routing baseline |

### Size-Aware Scanning Depth Heuristics

Defaults by graph size:
- Small (< 50,000 edges): default depth 3, max depth 4
- Medium (50,000 to 250,000 edges): default depth 2, max depth 3
- Large (> 250,000 edges): default depth 1, max depth 2

Fan-out caps:
- Depth 1: top 50 edges by confidence
- Depth 2: top 20 edges per node
- Depth 3+: top 8 edges per node

Pruning and safety:
- Always keep `BELONGS_TO_FRAMEWORK` and `IN_FAMILY` edges.
- Prune low-confidence `GUIDANCE_SUPPORTS_CONTROL` edges with size-adaptive thresholding.
- Stop traversal when marginal new-node gain is below 5% per hop.
- Enforce response/edge traversal caps and return truncation metadata when exceeded.

### Milestone 2 Exit Criteria

- [x] Local batch build is deterministic and idempotent.
- [x] Graph artifacts generated and validated.
- [x] Size-aware heuristics active and verified.
- [x] OpenAPI updates remain backward compatible.
- [x] Local query/build/export behaviour validated.

## Milestone 3: Azure Rollout

Status: In progress

Deliverables:
- [ ] Azure graph backend adapter implemented
- [ ] Query semantics parity with local baseline
- [ ] Build/query API contract parity validated
- [ ] Azure operational runbook drafted

Runbook progress:
- [x] Snapshot build/read configuration documented for Azure Blob-backed graph artifacts
- [x] Validation steps documented for publish + export/node read checks
- [x] Repeatable Azure smoke script added for graph snapshot build/read validation
- [ ] Native Azure backend operational procedures documented

Exit criteria:
- All local parity tests pass on Azure backend.
- Observability and audit events are available in Azure telemetry path.

## Milestone 4: AWS Rollout

Status: In progress

Deliverables:
- [ ] AWS graph backend adapter implemented
- [ ] Query semantics parity with local and Azure
- [ ] Build/query API contract parity validated
- [ ] AWS operational runbook drafted

Runbook progress:
- [x] Snapshot build/read configuration documented for AWS S3-backed graph artifacts
- [x] Repeatable AWS smoke script added for graph snapshot build/read validation
- [x] Dedicated AWS graph snapshot validation runbook added
- [ ] Native AWS backend operational procedures documented

Exit criteria:
- All parity and smoke checks pass on AWS backend.
- Performance and operational hardening complete.

## Milestone 5: Graph Query Experience

Status: Not started

Deliverables:
- [ ] RAG Query Console can trigger graph builds as an internal/admin workflow
- [ ] RAG Query Console can visualise graph nodes/edges with interactive filters
- [ ] Hybrid search can optionally expand through nearest-neighbour graph nodes
- [ ] API contract exposes graph-assisted search options for external query clients
- [ ] API contract exposes framework-scoped control retrieval

Notes:
- Graph build remains internal-only and must not be exposed through the published external gateway contract.
- Graph visualisation should support at least framework, node type, edge type, confidence, and depth filters.
- Nearest-neighbour expansion should be opt-in and bounded so it complements rather than replaces vector/keyword retrieval.

Lifecycle rules:
- Graph build is unavailable until controls are loaded.
- After Corpus A and/or Corpus B controls are loaded, graph build may be triggered manually from the console.
- Graph build may be rerun progressively as new controls are added, but it remains a manual operator action.
- Graph visualisation is only available when a built graph exists.
- Ask graph expansion is only available when a built graph exists.
- If controls are removed, the graph becomes invalid and must be rebuilt.

Candidate work items:
- [ ] T5-01 Internal graph build UX in RAG Query Console
- [ ] T5-02 Graph visualisation endpoint/response shaping for UI
- [ ] T5-03 Graph visualisation filters and truncation UX
- [ ] T5-04 Ask/API request model support for graph neighbour expansion
- [ ] T5-05 Retrieval pipeline integration for graph-assisted hybrid search
- [ ] T5-06 Contract and tests for framework controls listing endpoint
- [ ] Future: graph build job/status API for staged rebuilds
- [ ] Future: staged snapshot publish/commit flow for atomic graph swaps

Progress:
- [x] Baseline framework controls listing endpoint implemented at `/api/frameworks/{framework}/controls`
- [x] External OpenAPI contract updated for framework controls listing
- [x] Endpoint and contract tests added for framework controls listing
- [x] Ask/API request surface extended with bounded graph neighbour expansion options
- [x] Retrieval pipeline expands neighbours during hybrid search when enabled
- [x] RAG Query Console includes a graph explorer with snapshot/subgraph loading and client-side filters
- [x] Console graph build action now sources from the loaded Corpus A/B controls indexes directly rather than Ask results
- [x] Console graph build status banner and last-successful-build timestamp are shown in the RAG Query Console
- [x] Graph visualisation moved to a dedicated Graph tab with SVG node/edge rendering and existing filter controls
- [x] Graph tab visualisation supports pan/zoom, hover highlight, and click-to-focus neighborhood interactions
- [x] Graph tab supports node jump-by-ID, edge-type colour legend, and switchable layout modes (radial/force-like)
- [x] Graph tab provides computed semantic communities list and community-based graph filtering
- [x] Graph tab supports optional community isolation mode (dims non-selected communities)
- [x] Community summaries are generated and exposed in OpenAPI graph payloads, included in hybrid graph-assisted responses, and displayed on community selection in the Graph tab
- [x] Graph build computes a hyper-connected-node indicator and Graph tab shows a warning after build completion when threshold breaches are detected

## Contract and API Deliverables

- [ ] Extend [docs/contracts/rag-api-v1.openapi.yaml](docs/contracts/rag-api-v1.openapi.yaml) with optional graph fields in ask responses.
- [ ] Add external graph query/export endpoints; keep build internal-only.
- [ ] Preserve compatibility for existing consumers of `/api/ask`.

## Test Deliverables

- [ ] Unit tests for ontology mapping and deterministic IDs
- [ ] Unit tests for dedupe behaviour
- [ ] Unit tests for size-aware depth heuristics
- [ ] Integration tests for local batch build
- [ ] OpenAPI/contract validation tests
- [ ] Cross-backend parity smoke tests (local -> Azure -> AWS)

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Over-traversal on large graphs | High query latency and payload blowout | Enforce depth/fan-out caps and truncation metadata |
| Non-deterministic edge generation | Inconsistent graph outputs across runs | Stable IDs, deterministic sort order, idempotent dedupe keys |

## Future Considerations: GraphRAG and LangChain Interop

Purpose:
- Capture strategic options for adopting parts of Microsoft GraphRAG or LangChain without destabilising current milestone scope.

Decision framing:
- Current implementation is graph-capable and domain-specific, with explicit operational controls.
- GraphRAG is a methodology/pipeline that can improve hierarchical community reasoning and global query behaviour.
- LangChain is an orchestration framework that can reduce wiring effort but does not replace domain graph design decisions.

Interop matrix:

| Capability area | Keep current custom approach | Adopt GraphRAG-style patterns | Adopt LangChain components |
|---|---|---|---|
| Deterministic graph schema and IDs | Keep as-is for compliance reproducibility and audit parity | Keep custom IDs even if GraphRAG indexing is introduced | Keep custom; orchestration choice does not change schema authority |
| Community detection and summaries | Current connected-component grouping + concise summaries | Add hierarchical communities and multi-level community reports for global reasoning | Use summarisation chains/runnables only; community logic remains custom |
| Query modes | Current hybrid + bounded neighbour expansion | Add GraphRAG-like local/global query routing and map-reduce synthesis | Use routing chains to choose retrieval mode; preserve current guards |
| Operational lifecycle (build gating, swap safety) | Keep strict operator gating and no-replace-until-complete semantics | Preserve current lifecycle rules; do not relax build controls | Framework should not own lifecycle policy; keep in service layer |
| UI graph exploration | Continue iterative Graph tab improvements | Optionally surface hierarchical community drilldowns | UI unchanged; LangChain is backend orchestration only |
| Prompt/policy controls | Keep current guardrails and precedence policy integration | Reuse policy layers with GraphRAG retrieval outputs | Keep existing policy middleware; integrate at chain boundaries |

Recommended incremental path (future):
- Phase A: Keep current graph schema and lifecycle controls; trial hierarchical community generation on top of existing graph payloads.
- Phase B: Introduce optional query routing (local vs global graph reasoning) behind feature flags.
- Phase C: Evaluate LangChain only for orchestration simplification where it does not weaken current policy/audit guarantees.

Out-of-scope for current milestone:
- Full migration to GraphRAG-native indexing/query stack.
- Replacement of existing endpoint/service orchestration with LangChain-first architecture.

Trigger conditions for revisiting this decision:
- Hyper-connected node indicator:
	- Track `hyper_connected_node_ratio` as the percentage of nodes whose degree is greater than or equal to `max(25, 5x median node degree)`.
	- Trigger architecture review when either condition holds for 2 consecutive weekly snapshots:
		- `hyper_connected_node_ratio >= 2%`, or
		- top-1 node degree accounts for `>= 10%` of total graph edges.
	- Expose this indicator in graph diagnostics/status reporting so emerging hub concentration is visible to operators.
- Retrieval quality plateau:
	- If grounded answer quality (internal evaluator + manual review) does not improve for 2 consecutive release cycles despite prompt/retrieval tuning, initiate a GraphRAG pattern trial.
- Cross-document/global reasoning gaps:
	- If more than 15% of reviewed assessment tasks require synthesis across multiple disconnected communities and current local-neighbour expansion underperforms, evaluate local/global query routing.
- Latency pressure:
	- If p95 end-to-end ask latency exceeds target SLO by 20% for 2 consecutive weeks under expected load, evaluate routing/aggregation strategies before expanding retrieval depth further.
- Token/cost pressure:
	- If average token usage or cost per assessed request increases by 25% quarter-over-quarter without corresponding quality gains, evaluate hierarchical summarisation and condensed community reports.
- Operational burden:
	- If graph orchestration and pipeline maintenance consumes >20% of sprint capacity for 2 consecutive sprints, evaluate selective LangChain orchestration adoption for non-policy-critical paths.
- Governance regression risk:
	- Any proposed framework adoption must demonstrate parity with existing policy controls (precedence rules, guardrails, audit traceability) before production rollout.

Hyper-connected node mitigation playbook (future options):
- Adaptive hierarchical depth pruning based on community size and hub degree.
- Forced sub-community partitioning around large hubs before retrieval expansion.
- Map-reduce summarisation for hub-adjacent neighborhoods to cap context growth.
- Edge-type-aware fan-out caps (stricter caps on high-branching inferred edge types).
- Query-time hub demotion/penalty in neighbour expansion ranking when dominance exceeds threshold.
| Backend semantic drift | Contract inconsistency across environments | Parity suite and rollout gates by priority order |
| Contract breakage for existing clients | Integration failures | Optional fields only, staged API rollout, compatibility tests |

## Change Log

- 2026-07-05: Initial tracker created from approved plan and milestone board.
- 2026-07-06: Milestone 1 foundation artifacts added (ontology, deterministic IDs, dedupe keys, graph schema version, config thresholds).
- 2026-07-06: Milestone 2 started. T2-01 and T2-02 implemented with graph artifact schemas and deterministic A/B node record builders.
- 2026-07-06: T2-03 implemented with structural/inferred edge builders and idempotent edge dedupe logic.
- 2026-07-06: T2-04 implemented with size-aware traversal heuristics (depth/fan-out/pruning/truncation metadata) and unit tests.
- 2026-07-06: T2-05 implemented with local artifact generation (`nodes.jsonl`, `edges.jsonl`, `graph_build_report.json`) and deterministic output tests.
- 2026-07-06: T2-06 implemented with SQLite graph persistence/query baseline and artifact ingest tests.
- 2026-07-06: T2-07 implemented with local graph build/query/export endpoints, OpenAPI contract updates, and AskResponse graph fields.
- 2026-07-06: T2-08 implemented with graph operation metrics and endpoint audit payload emission.
- 2026-07-06: T2-09 completed with contract tests for graph OpenAPI paths and AskResponse optional graph fields, plus determinism/heuristics regression validation.
- 2026-07-06: T2-10 completed with graph backend interface/factory, `GRAPH_BACKEND` config routing (local default), explicit 503 backend-unavailable handling for non-local backends, and readiness tests.
- 2026-07-06: Milestone 2 formally closed (all exit criteria satisfied) and Milestone 3 kicked off with backend factory registry support plus dedicated backend-resolution tests.
- 2026-07-06: Milestone 3 progress: added Azure backend scaffold with explicit `GRAPH_AZURE_EMULATION_ENABLED` local-parity mode, retaining safe default behaviour (`azure` backend unavailable unless emulation is explicitly enabled).
- 2026-07-06: Milestone 3 progress: added graph endpoint parity tests covering `GRAPH_BACKEND=azure` with emulation enabled, validating build/node/related/export response-shape parity against local backend.
- 2026-07-06: Milestone 3 progress: added artifact-snapshot-backed Azure adapter mode via `GRAPH_AZURE_ARTIFACTS_DIR`, enabling first non-emulation read-path parity checks (`counts`, `get_node`, `export`/`related` refresh source) while keeping Azure default behaviour opt-in.
- 2026-07-06: Milestone 3 progress: added API-level tests for `GRAPH_BACKEND=azure` with `GRAPH_AZURE_ARTIFACTS_DIR`, validating that node/related/export endpoints can serve persisted local artifacts through the Azure snapshot adapter path.
- 2026-07-06: Milestone 3 progress: removed SQLite dependency from Azure artifact-snapshot reads; the snapshot adapter now serves `counts`, `get_node`, `subgraph`, and `export_graph` directly from validated JSONL-backed in-memory state.
- 2026-07-06: Milestone 3 progress: introduced graph snapshot source abstractions for filesystem and object storage, plus config/env seams for Azure artifact container/prefix selection and stub-backed object storage parity tests.
- 2026-07-06: Milestone 3 progress: wired optional `graph_azure_storage_client` injection through graph endpoints and app registration, with endpoint tests validating Azure object-storage snapshot reads via container/prefix configuration.
- 2026-07-06: Milestone 3 progress: added opt-in Azure graph artifact publishing on build (`GRAPH_AZURE_PUBLISH_ENABLED`) so `POST /api/graph/build` can upload `nodes.jsonl`, `edges.jsonl`, and `graph_build_report.json` to configured container/prefix storage for Azure snapshot-backed consumption.
- 2026-07-06: Milestone 3 progress: documented Azure graph snapshot build/read runbook steps, including required env vars, published object keys, validation commands, and current rollout limitations.
- 2026-07-06: Milestone 3 progress: added a copy-paste Azure graph snapshot environment example to the Foundry setup guide for Container App operators.
- 2026-07-06: Milestone 3 progress: added `ops/scripts/azure/run-query-web-graph-smoke.sh` for repeatable private-network smoke validation of Azure graph build publish and snapshot-backed export/node/related reads.
- 2026-07-06: Milestone 3 progress: added `ops/scripts/azure/graph-smoke-payload.sample.json` so operators can run the Azure graph smoke flow without composing a payload manually.
- 2026-07-06: Milestone 3 progress: added a regression test to ensure `ops/scripts/azure/graph-smoke-payload.sample.json` remains valid for `GraphBuildRequest` and executable through `POST /api/graph/build`.
- 2026-07-06: Milestone 3 progress: added `QUERY_GRAPH_PREFLIGHT_ONLY=true` support to the Azure graph smoke script so operators can validate the sample payload locally before calling a deployed endpoint.
- 2026-07-06: Milestone 4 started: added AWS snapshot-backed graph adapter routing, app S3 client wiring, and build publish/read parity tests using `GRAPH_BACKEND=aws` with S3 bucket/prefix configuration.
- 2026-07-06: Milestone 4 progress: added `ops/scripts/aws/run-query-web-graph-smoke.sh` plus `ops/scripts/aws/graph-smoke-payload.sample.json`, and documented AWS graph snapshot smoke validation in the AWS deployment guide.
- 2026-07-06: Milestone 4 progress: added a regression test to ensure `ops/scripts/aws/graph-smoke-payload.sample.json` remains valid for `GraphBuildRequest` and executable through `POST /api/graph/build`.
- 2026-07-06: Contract update: removed `POST /api/graph/build` from the published external OpenAPI spec so external consumers can query/export graph data but cannot trigger graph builds through the documented gateway contract.
- 2026-07-06: Milestone 4 progress: added [docs/aws-graph-snapshot-validation.md](docs/aws-graph-snapshot-validation.md) as the dedicated AWS graph snapshot runbook, covering env vars, published objects, manual validation, smoke tooling, and rollout limitations.
- 2026-07-06: Additional requirements captured for follow-on graph query experience work: internal build trigger in the RAG Query Console, graph visualisation with filters, opt-in nearest-neighbour graph expansion in hybrid search, and framework-scoped control retrieval in the API contract.
- 2026-07-06: Milestone 5 progress: added `/api/frameworks/{framework}/controls` as a framework-scoped controls retrieval endpoint, wired it into the app, published it in the external contract, and added focused endpoint/contract tests.
- 2026-07-06: Milestone 5 progress: added graph neighbour expansion request fields to the shared ask model, API contract, RAG Query Console advanced settings, and ask request plumbing; retrieval behaviour remains a follow-up implementation step.
- 2026-07-06: Milestone 5 progress: implemented bounded graph-assisted retrieval expansion in the RAG pipeline so nearest-neighbour controls and guidance can supplement vector/keyword results when requested, with focused pipeline, ask, and contract tests.
- 2026-07-06: Milestone 5 progress: added a Relationship Graph Explorer to the RAG Query Console Reference tab, allowing operators to load graph snapshots or node-centred subgraphs and filter by framework, node type, edge type, and confidence.