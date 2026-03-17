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

### `--mode reset` (on-demand data reset)

Removes loaded indexed data without destroying Azure resources.

**What it does:**

1. Deletes all documents from the target Search index.
2. Resets Search indexer state so unchanged blobs can be reprocessed.
3. Optionally deletes all source blobs from the storage container.

Examples:

```bash
# Reset only indexed documents (preserve source blobs)
python3 -m ingestion.runner --mode reset

# Reset indexed documents and purge source blobs
python3 -m ingestion.runner --mode reset --purge-blobs
```

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

All role assignments and compute resources are managed by Terraform.  Two
sequential applies are needed because the Search service MI principal ID is not
known until the first apply completes:

```bash
cd infra/terraform

# Apply 1: enable Search system-assigned identity, provision ACR,
#          Container App Environment, attach MI to jumpbox
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars \
                -var-file=environments/dev/dev.tfvars

# Apply 2: create Search MI role assignments (principal ID now known)
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars \
                -var-file=environments/dev/dev.tfvars
```

Resources provisioned:

| Resource | Name | Purpose |
|---|---|---|
| `azurerm_container_registry` (Premium) | `acr-dev-aue-001` | Private image registry |
| `azurerm_container_app_environment` | `cae-dev-aue-001` | VNet-integrated Container Apps environment |
| `azurerm_container_app_job` | `caj-ingestion-dev-aue-001` | Manually-triggered ingestion job (optional; gated by `enable_ingestion_job`) |

Roles assigned to the **user-assigned MI** (`id-agent-runtime-dev-aue-001`):

| Role | Scope | Purpose |
|---|---|---|
| `Storage Blob Data Contributor` | Storage account | Upload and read source files |
| `Search Service Contributor` | Search service | Create or update the index, data source, skillset, and indexer |
| `Search Index Data Contributor` | Search service | Write index documents |
| `Cognitive Services User` | Foundry account | OCR skill enrichment |
| `AcrPull` | Container registry | Pull ingestion image |

Roles assigned to the **Search service system-assigned MI**:

| Role | Scope | Purpose |
|---|---|---|
| `Storage Blob Data Reader` | Storage account | Blob indexer data source access |
| `Cognitive Services OpenAI User` | Foundry account | Embedding skill (MI auth) |

The user-assigned MI is also attached to the jumpbox VM, so `DefaultAzureCredential`
resolves automatically when running ingestion interactively on the jumpbox.

`enable_ingestion_job` defaults to `false` so infra can deploy before the
`ingestion-runner:latest` image exists in ACR.  After pushing the image from a
VNet-connected runner, set `enable_ingestion_job = true` and apply again.

### Private network constraint

All Azure resources have `public_network_access_enabled = false`.  
**`--mode azure` cannot reach any endpoint from outside the VNet.**

| Option | When to use |
|---|---|
| **Container App Job** (recommended) | Production; trigger via `az containerapp job start`; runs inside the VNet |
| **Jumpbox** | Interactive dev/debug; SSH in via Azure Bastion; MI already attached |
| **Dev IP exception** | Add a conditional IP-based firewall rule in Terraform (variable flag) for the dev machine |

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

---

## Container App Deployment (production)

### 1. Build and push the image

Run from inside the VNet (jumpbox or CI runner with VNet injection) because the
ACR has no public access:

```bash
# From the jumpbox or a VNet-connected CI runner
cd /path/to/sc3-Azure-Foundry-RAG
chmod +x ops/scripts/build-push-ingestion.sh
ENV=dev IMAGE_TAG=latest ./ops/scripts/build-push-ingestion.sh
```

The script resolves the ACR login server from `ACR_LOGIN_SERVER`, Terraform
output, or the deterministic naming pattern `acr<env><location_short><instance>.azurecr.io`,
then runs `docker build` + `docker push`.

### 2. Upload source documents to blob storage

```bash
STORAGE_NAME=$(cd infra/terraform && terraform output -raw storage_account_name)

az storage blob upload-batch \
  --account-name "${STORAGE_NAME}" \
  --destination grounding-data \
  --source ./runtime/samples \
  --auth-mode login
```

### 3. Trigger the Container App Job

```bash
JOB_NAME=$(cd infra/terraform && terraform output -raw container_app_job_name)

# Provision pipeline and index files already in blob (default args: --skip-upload)
az containerapp job start \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev

# Or upload + index in one step by overriding the default args
az containerapp job start \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --args '--mode' 'azure' '--input-dir' '/path/to/files'
```

### 4. Check job status and logs

```bash
# List recent executions
az containerapp job execution list \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --output table

# Get details for the most recent execution
EXEC_NAME=$(az containerapp job execution list \
  -n "${JOB_NAME}" -g rg-ai-platform-dev \
  --query "[0].name" -o tsv)

az containerapp job execution show \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --job-execution-name "${EXEC_NAME}" \
  -o yaml

# Optional: stream current app logs (CLI support varies by extension version)
az containerapp logs show \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --follow
```

### 5. Verify deployed container and execution

```bash
# Confirm the pushed image tag exists in ACR
az acr repository show-tags \
  --name acrdevaue001 \
  --repository ingestion-runner \
  --top 10 \
  --orderby time_desc \
  -o table

# Confirm the Container App Job is using the expected image
az containerapp job show \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --query "properties.template.containers[0].image" \
  -o tsv

# Confirm the latest execution status
az containerapp job execution list \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --query "[0].{name:name,status:status,startTime:properties.startTime}" \
  -o table
```

### Troubleshooting CLI mismatches

```bash
# Managed identity login on newer Azure CLI versions:
# --username is no longer supported for MI login.
az login --identity --client-id <user-assigned-mi-client-id>

# Some CLI/extension versions do not support:
#   az containerapp logs show --execution <name>
# Use job execution APIs instead:
EXEC_NAME=$(az containerapp job execution list \
  -n "${JOB_NAME}" -g rg-ai-platform-dev \
  --query "[0].name" -o tsv)

az containerapp job execution show \
  -n "${JOB_NAME}" \
  -g rg-ai-platform-dev \
  --job-execution-name "${EXEC_NAME}" \
  -o yaml
```

---

## Jumpbox (interactive)

The jumpbox VM has the `id-agent-runtime-dev-aue-001` managed identity attached,
so no credentials or `AZURE_CLIENT_ID` are needed — `DefaultAzureCredential`
picks up the single user-assigned MI automatically.

```bash
# SSH in via Azure Bastion (Standard/Premium SKU required for native client tunnel)
az network bastion ssh \
  --name bas-dev-aue-001 \
  --resource-group rg-ai-platform-dev \
  --target-resource-id <jumpbox-vm-id> \
  --auth-type ssh-key \
  --username azureuser \
  --ssh-key ~/.ssh/id_ed25519

# On the jumpbox — install Azure CLI, Python 3.12, and clone the repo (first time only)

# 1. Azure CLI (not installed by default on Ubuntu 22.04)
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# 2. Python 3.12 — not in default Ubuntu 22.04 repos; add the deadsnakes PPA
sudo apt-get update && sudo apt-get install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv python3-pip git
sudo git clone https://github.com/AndyArch11/sc3-Azure-Foundry-RAG /opt/sc3-ingestion
sudo chown -R azureuser:azureuser /opt/sc3-ingestion
cd /opt/sc3-ingestion/runtime
# Use python3.12 explicitly — python3 may default to 3.10 on Ubuntu 22.04
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Login with VM managed identity
az login --identity

# Set env vars (no credentials — MI handles auth)
export AZURE_SEARCH_ENDPOINT="https://srch-dev-aue-001.search.windows.net"
export AZURE_OPENAI_ENDPOINT="https://foundry-dev-aue-001.openai.azure.com"
export AZURE_STORAGE_ACCOUNT_NAME=$(az storage account list -g rg-ai-platform-dev --query "[0].name" -o tsv)
export AZURE_STORAGE_RESOURCE_ID=$(az storage account show -g rg-ai-platform-dev -n "$AZURE_STORAGE_ACCOUNT_NAME" --query id -o tsv)

# Upload samples and index
python3 -m ingestion.runner --mode azure --input-dir ./samples

# Or just re-index (files already in blob)

# Reset indexed data on demand (keeps Azure resources)
python3 -m ingestion.runner --mode reset

# Reset indexed data and source blobs
python3 -m ingestion.runner --mode reset --purge-blobs


```
.venv/bin/python -m pytest ../tests
```

