# Runtime Scaffold

This folder contains the document ingestion runtime.

## Ingestion Modes

Two modes are available under the same entry point.

### `--mode local` (default)

Client-side extraction using `pypdf` and `openpyxl`.  Useful for unit testing
and offline development.  Does not require Azure connectivity.

```bash
cd runtime

# Recommended: use an isolated virtual environment
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

python3 -m ingestion.runner \
  --mode local \
  --input-dir ./samples \
  --output-jsonl ./out/chunks.jsonl \
  --chunk-size 1200 \
  --chunk-overlap 200

# Run tests (from runtime directory) via venv interpreter
.venv/bin/python -m pytest ../tests
```

Supported formats in local mode: `.pdf`, `.xlsx`, `.xlsm`, `.xltx`, `.xltm`

---

### `--mode azure` (production)

Server-side enrichment via the Azure AI Search indexer pipeline.

**What it does:**

1. Uploads source documents to the `grounding-data` blob container.
2. Provisions the Search index, data source, skillset, and indexer if they
   do not exist (idempotent — safe to re-run).
3. Runs the indexer and waits for it to complete.

**Azure AI Search skills applied:**

| Skill | Purpose |
|---|---|
| `DocumentExtractionSkill` | Extracts text and normalised page images from raw file data (PDF, DOCX, XLSX, PPTX, HTML…) |
| `OcrSkill` | Runs OCR on normalised page images — handles scanned / image-only PDFs |
| `MergeSkill` | Merges native extracted text with OCR text into a single body |
| `SplitSkill` | Chunks the merged text into overlapping pages (configurable size / overlap) |
| `AzureOpenAIEmbeddingSkill` | Generates a dense vector embedding per chunk using the configured deployment |

Index projections produce **one Search document per chunk**, with stable chunk-level keys for idempotent re-indexing.

**Supported formats in azure mode:**  
`.pdf`, `.docx`, `.doc`, `.xlsx`, `.xlsm`, `.xltx`, `.xltm`, `.pptx`, `.ppt`, `.html`

---

## Required Environment Variables (azure mode)

| Variable | Example | Notes |
|---|---|---|
| `AZURE_SEARCH_ENDPOINT` | `https://srch-dev-aue-001.search.windows.net` | From Terraform output |
| `AZURE_OPENAI_ENDPOINT` | `https://foundry-dev-aue-001.openai.azure.com` | Foundry AI Services endpoint |
| `AZURE_STORAGE_ACCOUNT_NAME` | `stdevaue001sys6yd` | From Terraform output |
| `AZURE_STORAGE_RESOURCE_ID` | `/subscriptions/.../storageAccounts/stdevaue001sys6yd` | `az storage account show -g rg-ai-platform-dev -n <name> --query id -o tsv` |

| Variable | Default | Notes |
|---|---|---|
| `AZURE_SEARCH_INDEX_NAME` | `grounding-index` | |
| `EMBEDDING_DEPLOYMENT_NAME` | `text-embedding-ada-002` | Must match deployed model name |
| `EMBEDDING_DIMENSIONS` | `1536` | Match the embedding model |
| `AZURE_STORAGE_CONTAINER_NAME` | `grounding-data` | Pre-provisioned by Terraform |
| `AZURE_OPENAI_API_KEY` | _(unset)_ | Leave unset to use Search service managed identity |
| `COGNITIVE_SERVICES_API_KEY` | _(unset)_ | Leave unset for free tier (20 enrichments/run); set for larger production runs |
| `CHUNK_SIZE` | `1200` | Characters per chunk |
| `CHUNK_OVERLAP` | `200` | Overlap between adjacent chunks |

---

## Azure Mode Prerequisites

### Terraform applies required

All role assignments are managed by Terraform but require two sequential applies
because the Search service managed identity principal ID is not known until the
service is updated:

```bash
cd infra/terraform

# Apply 1: enable system-assigned identity on the Search service
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars \
                -var-file=environments/dev/dev.tfvars

# Apply 2: create role assignments now that the principal ID is known
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars \
                -var-file=environments/dev/dev.tfvars
```

Roles assigned by Terraform to the Search service system-assigned MI:

| Role | Scope | Purpose |
|---|---|---|
| `Storage Blob Data Reader` | Storage account | Blob indexer data source access |
| `Cognitive Services OpenAI User` | Foundry / AI Services account | AzureOpenAIEmbeddingSkill (MI auth) |

### Private network constraint

All Azure resources are provisioned with `public_network_access_enabled = false`.
**`--mode azure` cannot reach any endpoint from outside the VNet.**

To run `--mode azure`, choose one of:

- **Jumpbox (recommended)**: SSH into `vm-jumpbox-dev-aue-001` via Azure Bastion
  and run ingestion from there.  Private DNS resolves all endpoints to 10.20.1.x.
- **Dev network exception**: add a conditional IP-based network rule to the
  storage account and Search service for the dev machine's outbound IP (a Terraform
  variable flag is the clean way to do this).

---

## Quick Start (azure mode)

```bash
# Retrieve values from Terraform outputs
SEARCH_EP=$(cd infra/terraform && terraform output -raw search_endpoint 2>/dev/null || \
  az search service show -g rg-ai-platform-dev -n srch-dev-aue-001 --query "properties.endpoint" -o tsv)

STORAGE_NAME=$(cd infra/terraform && terraform output -raw storage_account_name 2>/dev/null || \
  az storage account list -g rg-ai-platform-dev --query "[0].name" -o tsv)

STORAGE_ID=$(az storage account show -g rg-ai-platform-dev -n "$STORAGE_NAME" --query id -o tsv)

FOUNDRY_EP="https://foundry-dev-aue-001.openai.azure.com"

# Set env vars
export AZURE_SEARCH_ENDPOINT="$SEARCH_EP"
export AZURE_OPENAI_ENDPOINT="$FOUNDRY_EP"
export AZURE_STORAGE_ACCOUNT_NAME="$STORAGE_NAME"
export AZURE_STORAGE_RESOURCE_ID="$STORAGE_ID"

# (Optional) use API key auth for embedding skill in sandbox
export AZURE_OPENAI_API_KEY=$(az cognitiveservices account keys list \
  -g rg-ai-platform-dev -n foundry-dev-aue-001 --query key1 -o tsv)

# Run
cd runtime
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m ingestion.runner --mode azure --input-dir ./samples

# Optional: verify tests from runtime directory
.venv/bin/python -m pytest ../tests
```

