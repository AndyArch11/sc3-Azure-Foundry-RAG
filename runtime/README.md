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

## Controls Pipeline (Pre-parsed Standards)

Pre-parsed control records (for example Essential Eight requirement JSONL) should be
loaded into a dedicated Search index, separate from the evidence chunk index.

Use the controls runner:

```bash
cd runtime
source .venv/bin/activate

# Parse only (writes JSONL to ./parsed-controls)
python3 -m ingestion.controls_runner --mode parse --framework essential_eight

# Parse all supported frameworks in one run
python3 -m ingestion.controls_runner --mode parse --framework all

# Publish an existing JSONL file to the controls index
python3 -m ingestion.controls_runner --mode publish \
  --input-jsonl ../parsed-controls/essential_eight_november-2023.jsonl

# End-to-end parse + ensure index + publish
python3 -m ingestion.controls_runner --mode parse-and-publish \
  --framework essential_eight

# End-to-end parse + publish all supported frameworks
python3 -m ingestion.controls_runner --mode parse-and-publish \
  --framework all
```

Controls index environment variables:

| Variable | Default | Notes |
|---|---|---|
| `AZURE_SEARCH_ENDPOINT` | _(required)_ | Same Search service used by evidence index |
| `AZURE_SEARCH_CONTROLS_INDEX_NAME` | `controls-index` | Dedicated index for requirement records |

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
| `QUERY_WEB_REQUIRED_GROUP_OBJECT_ID` | _(unset)_ | Optional Entra security group object ID required by query web app |

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

### Roll Out The Images

For standard private-network deployments, run non-RBAC rollout from jumpbox:

If Entra group-gated query web auth is enabled, first create or rotate the
EasyAuth app credential and write it to your private Key Vault:

```bash
# Run once per environment to provision private app-secrets Key Vault + PE:
./ops/scripts/phase3c-app-secrets.sh "${TARGET_ENV}" apply

sudo ./ops/scripts/configure-query-web-easyauth-secret.sh "${TARGET_ENV}" \
  --secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

```bash
sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "${INGESTION_TAG}" \
  --query-web-tag "${QUERY_TAG}" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

**IMPORTANT:** When rolling out only the ingestion container (omitting `--query-web-tag`), you must still include the `--entra-secret-kv` and `--entra-secret-name` arguments if the query-web container has previously been deployed. Omitting these arguments will remove the web app's EasyAuth authentication configuration and break access to the query endpoint.

**Example for ingestion-only rollout:**
```bash
sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "${INGESTION_TAG}" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

**TODO:** Refactor container deployments to be independent—ingestion and query-web should have separate rollout paths so that updating one does not require specifying configuration for the other.

Then reconcile RBAC resources from an admin identity:

```bash
# Run from admin context (local admin shell or CI), not jumpbox UAMI context.
./ops/scripts/reconcile-rbac-admin.sh "${TARGET_ENV}" apply
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

./ops/scripts/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --az-login-client-id "<agent-runtime-uami-client-id>" --run-unit-tests
```

If exactly one user-assigned managed identity is attached to the VM, `--az-login-client-id` can be omitted and the script auto-discovers it.

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

# Use this only when multiple UAMIs are attached and you must force one identity.
# ./ops/scripts/configure-jumpbox.sh \
#   --install-terraform \
#   --install-azure-cli \
#   --az-login-identity \
#   --az-login-client-id "<agent-runtime-uami-client-id>" \
#   --run-unit-tests

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