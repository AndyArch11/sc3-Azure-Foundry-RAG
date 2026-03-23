# Runtime Guide

This folder contains the document ingestion runtime used by the platform's Container App Job and jumpbox workflows.

## Ingestion Modes

Two modes are available under the same entry point.

### `--mode local`

Client-side extraction using `pypdf` and `openpyxl`. Use this for unit testing and offline development.

```bash
cd runtime

sudo python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r ../requirements-dev.txt

python3 -m ingestion.runner \
  --mode local \
  --input-dir ./samples \
  --output-jsonl ./out/chunks.jsonl \
  --chunk-size 1200 \
  --chunk-overlap 200

.venv/bin/python -m pytest ../tests/unit -q
```

Supported formats in local mode: `.pdf`, `.xlsx`, `.xlsm`, `.xltx`, `.xltm`

### `--mode azure`

Server-side enrichment via the Azure AI Search indexer pipeline.

What it does:

1. Uploads source documents to the `grounding-data` blob container.
2. Provisions the Search index, data source, skillset, and indexer if they do not exist.
3. Runs the indexer and waits for completion.

Applied Search skills:

| Skill | Purpose |
|---|---|
| `DocumentExtractionSkill` | Extracts text and normalised page images from raw files |
| `OcrSkill` | Adds OCR for scanned or image-only content |
| `MergeSkill` | Combines native extraction with OCR output |
| `SplitSkill` | Chunks merged text into overlapping pages |
| `AzureOpenAIEmbeddingSkill` | Generates a vector embedding per chunk |

Supported formats in azure mode: `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xlsm`, `.xltx`, `.xltm`, `.pptx`, `.ppt`, `.html`

### `--mode reset`

Removes indexed data without destroying Azure resources.

```bash
# Reset only indexed documents
python3 -m ingestion.runner --mode reset

# Reset indexed documents and purge source blobs
python3 -m ingestion.runner --mode reset --purge-blobs
```

## Required Environment Variables For Azure Mode

| Variable | Example | Notes |
|---|---|---|
| `AZURE_SEARCH_ENDPOINT` | `https://srch-<env>-<location>-001.search.windows.net` | Prefer Terraform output |
| `AZURE_OPENAI_ENDPOINT` | `https://foundry-<env>-<location>-001.openai.azure.com` | Foundry AI Services endpoint |
| `AZURE_STORAGE_ACCOUNT_NAME` | `st<env><location><suffix>` | Prefer Terraform output |
| `AZURE_STORAGE_RESOURCE_ID` | `/subscriptions/.../storageAccounts/<name>` | Resolve with Azure CLI |

| Variable | Default | Notes |
|---|---|---|
| `AZURE_SEARCH_INDEX_NAME` | `grounding-index` | |
| `EMBEDDING_DEPLOYMENT_NAME` | `text-embedding-ada-002` | Must match deployed model name |
| `EMBEDDING_DIMENSIONS` | `1536` | Match the embedding model |
| `AZURE_STORAGE_CONTAINER_NAME` | `grounding-data` | Pre-provisioned by Terraform |
| `AZURE_OPENAI_API_KEY` | _(unset)_ | Leave unset for managed identity flow |
| `COGNITIVE_SERVICES_API_KEY` | _(unset)_ | Optional for larger OCR/enrichment runs |
| `CHUNK_SIZE` | `1200` | Characters per chunk |
| `CHUNK_OVERLAP` | `200` | Overlap between adjacent chunks |

## Prerequisites

### Terraform State And Core Platform

Provision the target environment before running azure mode:

```bash
./ops/scripts/phase1-bootstrap.sh <env>
./ops/scripts/phase2-network-dns.sh <env> apply
./ops/scripts/phase3-data-ai.sh <env> apply
```

For runtime deployment, the environment tfvars usually need:

- `enable_ingestion_job = true`
- `enable_query_web_app = true`
- `query_web_public_endpoint = false` (default private endpoint) or `true` (public endpoint)

`enable_ingestion_job` is intentionally separate so infrastructure can be created before the ingestion image exists in ACR.

`query_web_public_endpoint` is a creation-level setting because it changes the
Container App Environment load balancer mode. Switching it later requires
re-creating the Container App Environment and hosted apps.

### Private Network Constraint

All Azure resources are deployed with public network access disabled where supported.

`--mode azure` must run from one of these locations:

- Container App Job inside the VNet
- Jumpbox reached through Bastion
- CI runner with private network connectivity into the VNet

## Quick Start For Azure Mode

Run these commands from the repository root on a host that can reach the private endpoints.

```bash
TARGET_ENV="<env>"   # dev, test, or prod
TF_DIR="infra/terraform"

terraform -chdir="${TF_DIR}" init \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"

RG_NAME=$(terraform -chdir="${TF_DIR}" output -raw resource_group_name)
SEARCH_EP=$(terraform -chdir="${TF_DIR}" output -raw search_endpoint)
STORAGE_NAME=$(terraform -chdir="${TF_DIR}" output -raw storage_account_name)
STORAGE_ID=$(az storage account show -g "${RG_NAME}" -n "${STORAGE_NAME}" --query id -o tsv)
FOUNDRY_EP=$(az cognitiveservices account list \
  -g "${RG_NAME}" \
  --query "[?kind=='AIServices'][0].properties.endpoint" \
  -o tsv)

cd runtime
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

export AZURE_SEARCH_ENDPOINT="${SEARCH_EP}"
export AZURE_OPENAI_ENDPOINT="${FOUNDRY_EP}"
export AZURE_STORAGE_ACCOUNT_NAME="${STORAGE_NAME}"
export AZURE_STORAGE_RESOURCE_ID="${STORAGE_ID}"

python3 -m ingestion.runner --mode azure --input-dir ./samples
```

If you need API-key-based embedding access for sandbox testing, resolve the Foundry account name first and then fetch a key:

```bash
FOUNDRY_NAME=$(az cognitiveservices account list \
  -g "${RG_NAME}" \
  --query "[?kind=='AIServices'][0].name" \
  -o tsv)

export AZURE_OPENAI_API_KEY=$(az cognitiveservices account keys list \
  -g "${RG_NAME}" \
  -n "${FOUNDRY_NAME}" \
  --query key1 -o tsv)
```

## Runtime Image Rollout

### Build And Push Images

Run from a Docker-capable host inside the VNet, because ACR has no public access:

```bash
TARGET_ENV="<env>"
INGESTION_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)"
QUERY_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)"

ENV="${TARGET_ENV}" IMAGE_TAG="${INGESTION_TAG}" ./ops/scripts/build-push-ingestion.sh
ENV="${TARGET_ENV}" IMAGE_TAG="${QUERY_TAG}" ./ops/scripts/build-push-query-web.sh
```

Use immutable tags rather than `latest` so Container Apps revisions roll forward predictably and Terraform plans remain stable.

### Roll Out The Images With Terraform

The build scripts print rollout commands. A full manual example is:

```bash
terraform -chdir=infra/terraform apply \
  -input=false \
  -var-file="environments/${TARGET_ENV}/bootstrap.generated.tfvars" \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars" \
  -var "ingestion_job_image_tag=${INGESTION_TAG}" \
  -var "query_web_image_tag=${QUERY_TAG}" \
  -target=module.agent_hosting
```

### Upload Documents To Blob Storage

```bash
STORAGE_NAME=$(terraform -chdir=infra/terraform output -raw storage_account_name)

az storage blob upload-batch \
  --account-name "${STORAGE_NAME}" \
  --destination grounding-data \
  --source ./runtime/samples \
  --auth-mode login
```

### Trigger And Inspect The Ingestion Job

```bash
RG_NAME=$(terraform -chdir=infra/terraform output -raw resource_group_name)
JOB_NAME=$(terraform -chdir=infra/terraform output -raw container_app_job_name)

az containerapp job start \
  -n "${JOB_NAME}" \
  -g "${RG_NAME}"

az containerapp job execution list \
  -n "${JOB_NAME}" \
  -g "${RG_NAME}" \
  --output table
```

To upload and index in one step, override the default job arguments:

```bash
az containerapp job start \
  -n "${JOB_NAME}" \
  -g "${RG_NAME}" \
  --args '--mode' 'azure' '--input-dir' '/path/to/files'
```

### Validate The Query Web App

```bash
QUERY_FQDN=$(terraform -chdir=infra/terraform output -raw query_web_fqdn)

QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/run-query-web-integration-tests.sh "https://${QUERY_FQDN}" "<optional-auth-token>"
```

If `query_web_auth_token` is configured in tfvars, pass the same token to the integration runner and any direct `curl` calls.

## Jumpbox Workflow

The jumpbox VM is provisioned on Ubuntu 22.04 and has the runtime user-assigned managed identity attached, so `DefaultAzureCredential` works without extra client ID configuration.

### Connect Through Bastion

```bash
az network bastion ssh \
  --name bas-<env>-<location>-001 \
  --resource-group rg-ai-platform-<env> \
  --target-resource-id <jumpbox-vm-id> \
  --auth-type ssh-key \
  --username azureuser \
  --ssh-key ~/.ssh/id_ed25519
```

### Prepare The Jumpbox

```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

Then run the repository bootstrap script:

```bash
git clone <your-repo-url> /opt/sc3-ingestion
cd /opt/sc3-ingestion

./ops/scripts/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --run-unit-tests
```

The script installs Docker, Python 3.12, Azure CLI, git, unzip, and other required OS packages, creates `runtime/.venv`, installs `requirements-dev.txt`, and optionally runs `./ops/scripts/install-terraform-local.sh`. It then authenticates with the managed identity, runs the unit test suite, and prints a smoke-check report across all installed components.

Flags that are not passed are reported as skipped in the smoke report rather than causing failures, so you can run a subset (for example, if Azure CLI is pre-installed by your base image):

### Run Ingestion Interactively On The Jumpbox

```bash
git clone <your-repo-url> /opt/sc3-ingestion
cd /opt/sc3-ingestion

# Full setup with Terraform, Azure CLI, managed identity login, and unit tests.
./ops/scripts/configure-jumpbox.sh \
  --install-terraform \
  --install-azure-cli \
  --az-login-identity \
  --run-unit-tests

cd runtime
source .venv/bin/activate

TARGET_ENV="<env>"
TF_DIR="../infra/terraform"

terraform -chdir="${TF_DIR}" init \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"

RG_NAME=$(terraform -chdir="${TF_DIR}" output -raw resource_group_name)
SEARCH_EP=$(terraform -chdir="${TF_DIR}" output -raw search_endpoint)
STORAGE_NAME=$(terraform -chdir="${TF_DIR}" output -raw storage_account_name)
STORAGE_ID=$(az storage account show -g "${RG_NAME}" -n "${STORAGE_NAME}" --query id -o tsv)
FOUNDRY_EP=$(az cognitiveservices account list \
  -g "${RG_NAME}" \
  --query "[?kind=='AIServices'][0].properties.endpoint" \
  -o tsv)

export AZURE_SEARCH_ENDPOINT="${SEARCH_EP}"
export AZURE_OPENAI_ENDPOINT="${FOUNDRY_EP}"
export AZURE_STORAGE_ACCOUNT_NAME="${STORAGE_NAME}"
export AZURE_STORAGE_RESOURCE_ID="${STORAGE_ID}"

python -m ingestion.runner --mode azure --input-dir ./samples
python -m ingestion.runner --mode reset
python -m ingestion.runner --mode reset --purge-blobs

.venv/bin/python -m pytest ../tests/unit -q
```

Use `requirements.txt` when you only need the ingestion runtime. Use `../requirements-dev.txt` when you also want to run the repository unit tests from the same environment, because those tests import both `runtime/` and `query_web/` modules.

If Docker is managed separately or not needed, use:

```bash
./ops/scripts/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --run-unit-tests --skip-docker
```

If you only need the ingestion runtime dependencies (no query-web or pytest), use:

```bash
./ops/scripts/configure-jumpbox.sh --runtime-only
```

Every component that is not installed by the options you pass is reported as `SKIP` in the smoke report rather than `FAIL`, so you can run any subset of flags safely.