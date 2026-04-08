# Search & OpenAI Integration Requirements

## Overview

The full compliance assessment workflow has two independent layers:

1. **Resource Extraction Layer** ✅ (No external dependencies)
   - Extracts resources, policies, and configurations from Azure using only ARM API
   - Uses `AzureMCPServer` from `runtime/assessment_orchestration/mcp/azure_resource.py`
   - Requires only: Azure authentication (CLI or SDK credentials)
   - **Status**: Validated working against live Azure resources (55 resources extracted from rg-ai-platform-dev)

2. **Framework Matching & Assessment Layer** ⚠️ (Requires Search + OpenAI)
   - Matches extracted resources against compliance frameworks (e.g., CIS, NIST)
   - Generates compliance findings and evidence citations
   - Requires: Azure Search Index + Azure OpenAI API

## Supported Components (Extraction Only)

The following components work **without Search/OpenAI**:

### Resource Enumeration
- ✅ List all resources in a resource group (ARM API enumeration)
- ✅ Retrieve resource properties and configuration
- ✅ Extract policy assignments and policy definitions
- ✅ Normalize resource metadata into AssessedArtifactPackage format

### Tested Coverage
- 55 different resource instances validated (extraction layer)
- Resource types: compute, networking, storage, database, identity, containers
- Policy assignments: Successfully retrieved from ARM API
- Policy definitions: Successfully fetched and included in artifact payload

**Smoke Test**: `tests/smoke_test_azure_extraction.py` validates extraction layer independently.

---

## Full Assessment Workflow (Search + OpenAI Required)

To enable the complete assessment pipeline (resource extraction → framework matching → compliance findings):

### Prerequisites

#### 1. Azure Search Service
Deploy or provide connection details:
```
- Service Name: <search-service-name>
- Endpoint: https://<search-service-name>.search.windows.net
- API Key: <search-admin-key>
- Index Name: compliance-framework-index
```

**Index Requirements**:
- Must contain embedded compliance controls from frameworks (CIS Benchmarks, NIST, PCI-DSS, etc.)
- Each control document should include:
  - `framework` field (e.g., "CIS Azure Foundations Benchmark v1.5.0")
  - `control_id` field (e.g., "1.1", "2.3.5")
  - `title` field (human-readable control name)
  - `description` field (full control text)
  - `embeddings` field (vector representation for semantic search)

**Data Loading**:
- Use `runtime/ingestion/` pipeline to index controls
- Supports controls from `parsed-controls/` directory
- Reference: `runtime/ingestion/controls_runner.py`

#### 2. Azure OpenAI API
Deploy or provide connection details:
```
- Endpoint: https://<openai-resource-name>.openai.azure.com/
- API Key: <openai-api-key>
- Deployment: compliance-assessment (or your deployment name)
- Model: gpt-4 or gpt-3.5-turbo
```

**LLM Usage**:
- Semantic matching between extracted resources and controls
- Generating compliance findings and recommendations
- Optional: Mistral-based control applicability review (see `docs/control-llm-review-integration.md`)

### Configuration

Add environment variables to enable full assessment:

```bash
# Search Service
AZURE_SEARCH_ENDPOINT=https://<service-name>.search.windows.net
AZURE_SEARCH_KEY=<admin-key>
AZURE_SEARCH_INDEX_NAME=compliance-framework-index

# OpenAI Service
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<api-key>

# Optional: LLM-based control applicability review with Mistral
CONTROL_LLM_REVIEW_ENABLED=true
CONTROL_LLM_REVIEW_HEURISTIC_THRESHOLD=0.70
```

### Runtime Wiring

The assessment orchestrator will automatically wire Search and OpenAI when configured:

```python
from runtime.assessment_orchestration.assessment_runtime import AssessmentRuntimeConfig

config = AssessmentRuntimeConfig(
    azure_search_enabled=True,  # Auto-detected from env
    control_llm_review_enabled=True,  # Optional; requires Mistral backend
)

# Search grounding will be used in:
# - retrieve_corpus_grounding() for control retrieval
# - SearchBackedAssessmentAgent.query_framework_controls()

# OpenAI will be used in:
# - LLM-based matching and finding generation
```

---

## Testing Full Workflow

### Step 1: Validate Extraction Layer (No Dependencies)
```bash
python tests/smoke_test_azure_extraction.py \
  --subscription-id <subscription-id> \
  --resource-group <resource-group> \
  --verbose
```

Expected output: ✅ 55 resources extracted, policy assignments retrieved

### Step 2: Validate Search Intelligence (Search Required)
```bash
# After Search Index is populated
python -c "
from runtime.assessment_orchestration.interfaces import SearchConfig
from runtime.ingestion.controls_runner import query_controls

config = SearchConfig(
    endpoint='https://<service>.search.windows.net',
    key='<key>',
    index_name='compliance-framework-index'
)
results = query_controls(config, 'network security')
print(f'Found {len(results)} controls')
"
```

### Step 3: Run Full Assessment (Search + OpenAI Required)
```python
from runtime.assessment_orchestration.azure_assessment import AzureAssessmentOrchestrator

orchestrator = AzureAssessmentOrchestrator(
    subscription_id='<subscription-id>',
    resource_group='<resource-group>',
    framework='CIS Azure Foundations Benchmark v1.5.0'
)

report = orchestrator.assess()
print(report.findings)  # Compliance findings with Search-backed evidence
```

---

## Deployment Phases

### Phase 1: Extraction Validation (✅ Complete - No Prerequisites)
- Extract resources from target scope
- Verify resource types and policy assignments
- Validate data normalization
- **Status**: Ready to test immediately with `smoke_test_azure_extraction.py`

### Phase 2: Framework Indexing (Search Prerequisites)
- Deploy Azure Search Service
- Load compliance control corpus
- Test semantic search against control documents
- **Effort**: ~1-2 days (depends on control corpus preparation)

### Phase 3: Assessment Generation (Search + OpenAI Prerequisites)
- Deploy Azure OpenAI Service (or use existing)
- Configure LLM backend (optional: Mistral for local dev)
- Run full orchestrator against test resource group
- **Effort**: ~1 day (wiring + integration testing)

### Phase 4: Production Rollout (All Prerequisites)
- Deploy to staging environment first
- Run multi-framework assessments (CIS, NIST, PCI-DSS)
- Monitor latency and cost (see `docs/control-llm-review-integration.md` for benchmarks)
- **Effort**: ~1-2 weeks (performance tuning + security review)

---

## Current Blockers

- **Search Service**: Not deployed in current environment
- **OpenAI Service**: Not deployed in current environment
- **Compliance Control Index**: Placeholder data; needs production corpus (CIS, NIST, PCI-DSS)

**Unblocked**:
- ✅ Resource extraction code (validated against live Azure)
- ✅ Framework matching logic (code path defined, awaiting Search)
- ✅ LLM enrichment (optional; Mistral integration ready, disabled by default)

---

## Next Steps to Unblock Full Assessment

1. **Short-term** (No infrastructure changes):
   - Use `smoke_test_azure_extraction.py` to validate resource extraction on any Azure scope
   - Verify resource types and policy context match expectations
   - Document findings for compliance analyst review

2. **Medium-term** (Infrastructure setup):
   - Provision Azure Search Service + OpenAI Service
   - Load compliance framework corpus into Search Index
   - Run integrated assessment tests

3. **Long-term** (Production hardening):
   - Performance tuning (caching, batching, cost optimization)
   - Multi-framework support (CIS, NIST, PCI-DSS assessed in parallel)
   - Audit trail and reporting (evidence retention, trend analysis)

---

## Cost Estimation (When Deployed)

| Component | Service | Estimated Cost | Notes |
|-----------|---------|-----------------|-------|
| Resource Extraction | ARM API | ~$0 | Minimal API calls, included in Azure subscription |
| Search Index | Azure Search | ~$50-200/month | Depends on index size and query volume |
| LLM Matching | Azure OpenAI | ~$0.10-1.00/assessment | ~2-5 LLM calls per resource (50+ resources typical) |
| Mistral Review (Optional) | Local Ollama | ~$0 | GPU compute cost only |

---

## References

- **Extraction Layer**: `runtime/assessment_orchestration/mcp/azure_resource.py`
- **Search Integration**: `runtime/assessment_orchestration/interfaces.py` (SearchConfig)
- **Assessment Orchestrator**: `runtime/assessment_orchestration/assessment_runtime.py`
- **Smoke Test**: `tests/smoke_test_azure_extraction.py`
- **LLM Integration**: `docs/control-llm-review-integration.md`
