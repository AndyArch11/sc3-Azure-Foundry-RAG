# Hosted AI Cyber Safety Platform

This repository provisions and operates a privately networked Azure AI Foundry solution for a cyber security target persona

## Scope

- Uses Azure-hosted AI Agent capabilities, with supporting ingestion/query runtime services deployed on self-managed Azure Container Apps.
- Deploys all platform resources via Terraform.
- Secures data plane services by private endpoints with public network access disabled.
- Supports ingestion and query agent workflows with configurable model defaults.

## Delivery Principles

- Code-only provisioning from an empty Azure tenant assumption.
- UK English documentation and naming conventions where platform APIs allow.
- Generic platform naming without legacy brand references.
- Managed identities and least-privilege access by default.
- Private networking first, with public network access disabled on supported services.

## What This Repository Contains

- Terraform runner container for deterministic infrastructure operations.
  - Storage account for terraform state files
  - Key Vault for jumpbox SSH secrets
- Modular Terraform layout for foundation, network, data services, private endpoints, observability, and agent hosting.
- Private networking model with:
  - VNet `/16`
  - Private endpoint subnet `/24`
  - Delegated agent subnet `/24`
  - Jumpbox subnet and Bastion host
  - Sizes based on [BYO private virtual network](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks). Modify for actual needs.
- Azure platform resources:
  - Storage account with blob container `grounding-data`
  - Azure AI Foundry with project and models
  - Azure AI Search
  - Azure Cosmos DB for chat conversations
- Private endpoint DNS zones and conditional forwarder guidance for:
  - `privatelink.azurecr.io`
  - `privatelink.blob.core.windows.net`
  - `privatelink.cognitiveservices.azure.com`
  - `privatelink.documents.azure.com`
  - `privatelink.file.core.windows.net`
  - `privatelink.openai.azure.com`
  - `privatelink.search.windows.net`
  - `privatelink.services.ai.azure.com`
- Logging and metrics foundations:
  - Log Analytics Workspace (LAW)
  - OpenTelemetry instrumentation baseline for runtime services
  - Prometheus-compatible metrics endpoint guidance with Azure Monitor integration path
- Unit-testing-first approach, with private-network integration tests designed to run from trusted network locations.

## Repository Layout

- `docs/` architecture, plans, and runbooks
- `infra/terraform/` bootstrap, environments, and reusable modules
- `ops/containers/terraform-runner/` Terraform execution container
- `ops/scripts/` shell scripts to bootstrap Terraform build environment, create Azure resources, and deploy and test containers.
- `query_web/` Docker container config for query UI running in an Azure Container App
- `runtime/` ingestion runtime and Docker container config for the Azure Container App job
- `tests/` unit and integration tests

## Runtime Functional Targets

- Ingest agent:
  - Chunks and indexes PDF and Excel sources.
  - Uses AI agent file search tooling patterns.
- Query agent:
  - Supports conversational retrieval-augmented generation.
  - Uses hybrid retrieval with reranking.
- Default model values (configurable):
  - Embedding model: `text-embedding-ada-002`
  - Query model: `gpt-5.1-chat`
  - Query evaluation model: `gpt-4.1-mini`

## Environment and Subscription Assumptions

- If an Azure free tenant already has a default subscription, that subscription is used.
- If no subscription context is available in automation, deployment fails fast with explicit guidance.
- Resource groups, networking, identities, and services are created by Terraform only.

## Operator Checklist

1. Set `TARGET_ENV` and update `infra/terraform/environments/<env>/<env>.tfvars`.
2. Run `./ops/scripts/phase1-bootstrap.sh <env>`, `./ops/scripts/phase2-network-dns.sh <env> apply`, and `./ops/scripts/phase3-data-ai.sh <env> apply`.
3. Build and push immutable ingestion and query image tags from a private-network-connected host.
4. Roll out those image tags through Terraform against `module.agent_hosting`.
5. Upload or ingest source documents, start the ingestion job, and load control data (for example Essential Eight) into the controls index.
6. Run `./ops/scripts/run-query-web-integration-tests.sh "https://<query-web-fqdn>" "<optional-auth-token>"` from inside the private network.

## Quick Start

1. Build the Terraform runner image:
   - `docker build -t tf-runner:local ops/containers/terraform-runner`
2. Or, if Docker is unavailable in your working environment, install Terraform locally:
   - `./ops/scripts/install-terraform-local.sh`
3. Choose an environment and update its tfvars file:
   - `infra/terraform/environments/<env>/<env>.tfvars`
4. Bootstrap remote state and supporting secrets:
   - `./ops/scripts/phase1-bootstrap.sh <env>`
5. Apply the core platform phases:
   - `./ops/scripts/phase2-network-dns.sh <env> apply`
   - `./ops/scripts/phase3-data-ai.sh <env> apply`
6. Build, push, and roll out runtime images as described in the Deployment runbook below.

## Documentation Index

- Detailed implementation sequencing: `docs/implementation-plan.md`
- Delivery slicing by phase: `docs/phases.md`
- Testing policy and private-endpoint test execution: `docs/testing-strategy.md`
- Logging and metrics baseline: `docs/observability.md`
- Foundry setup and deployment prerequisites: `docs/foundry-setup-guide.md`
- Conversation persistence and feedback flow: `docs/foundry-conversations.md`

## Current State

This repository contains deployable Terraform modules, a working ingestion runtime, a query web application with Foundry-backed conversations, and private-network integration tests. Delivery continues in phased increments, with private networking and security controls treated as non-negotiable constraints.

## Runtime Ingestion

- PDF and Excel ingestion runtime is available under `runtime/ingestion/`.
- Includes source extraction, deterministic chunking, and JSONL output generation for downstream search indexing.

## Deployment

In enterprise environments, deployment is normally automated through CI/CD and private-network-connected runners. The steps below are for manual operation where a pipeline is not yet in place. The provided instructions assumes running from a Linux environment using a shell CLI such as Bash.

Set the target environment once and reuse it throughout the runbook:

```bash
TARGET_ENV="<env>"   # dev, test, or prod
```

### Preconditions

1. Clone your fork or working repository and run commands from that clone.
2. Authenticate to Azure with an identity that can provision and update the target environment `az login`.
3. Use the environment-specific tfvars under `infra/terraform/environments/${TARGET_ENV}/` as the source of truth.
4. Run private-endpoint validation, image builds, and runtime smoke tests from a Docker-capable host with line of sight into the VNet, typically the jumpbox.

### Provision Infrastructure

You can either use the Terraform runner container or install Terraform locally:

1. Clone or update your repository:
   - Intitial Clone: `git clone [NAME-OF-REPO]`
   - Subsequent Pull: `git pull --ff-only`
2. Change directory into downloaded repo: `cd sc3-Azure-Foundry-RAG/`

- Build Terraform runner container:
  - `docker build -t tf-runner:local ops/containers/terraform-runner`
- Or install Terraform locally:
  - `./ops/scripts/install-terraform-local.sh`

Login to Azure
- `az login`

Deployment assumes that a target Azure subscription has already been created. Select target subscription to set target context for scripts.
- `az account set --subscription "target-subscription-name"`

Run the environment build scripts in order (can take over 1 hour to provision the Azure resources):

1. Create Azure resources required to support Terraform:
  - `./ops/scripts/phase1-bootstrap.sh "${TARGET_ENV}"`
2. Create Azure resources required to secure solution by private network (not required if bringing your own network, run phase 3 instead):
  - `./ops/scripts/phase2-network-dns.sh "${TARGET_ENV}" apply`
3. Optional Create Foundry related Azure resources (only required if BYOL network resources or wanting the jumpbox/bastion host):
  - `./ops/scripts/phase3-data-ai.sh "${TARGET_ENV}" apply`
4. Create private app secrets Key Vault and private endpoint:
  - `./ops/scripts/phase3c-app-secrets.sh "${TARGET_ENV}" apply`
5. Optional preview-only hosted agent path (ignore this step unless wanting to play with hosted agents - untested code): 
  - `ENABLE_HOSTED_QUERY_AGENT_PREVIEW=true ./ops/scripts/phase3b-agent-hosting.sh "${TARGET_ENV}" apply`

#### Optional install verification

```bash
sudo python3 -m venv runtime/.venv
source runtime/.venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-dev.txt
```

Run unit tests before releasing runtime changes:

- `python3 -m pytest tests/unit -q`

### Jumpbox Access

For manual private-network operations, connect through Bastion to the jumpbox using:

- Authentication Type: SSH Private Key from Azure Key Vault
- username: `azureuser`
- Key Vault resource group: `rg-tfstate-${TARGET_ENV}`
- Key Vault: `kvtfstate<xxxxxx>`
- private key secret: `jumpbox-admin-ssh-private-key-${TARGET_ENV}`

On the jumpbox:

1. Set the target environment for the current shell session:
  - `TARGET_ENV="<env>"   # dev, test, or prod`
2. Clone or update your repository:
  - Intitial Clone: `git clone [NAME-OF-REPO]`
  - Subsequent Pull: `git pull --ff-only`
3. Change directory into downloaded repo: `cd sc3-Azure-Foundry-RAG/`
4. a) Run the jumpbox bootstrap helper (default path; auto-discovers a single attached UAMI):
  - `sudo ./ops/scripts/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --init-terraform-backend "${TARGET_ENV}" --run-unit-tests`
4. b) If the VM has multiple user-assigned identities, pass the intended client ID explicitly:
  - `sudo ./ops/scripts/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --az-login-client-id "<agent-runtime-uami-client-id>" --init-terraform-backend "${TARGET_ENV}" --run-unit-tests`
5. If using Entra group-gated query web auth, run the external/admin Entra bootstrap first to create the query web app registration target:
  - `./ops/scripts/rollout-query-web-entra.sh "${TARGET_ENV}" apply`
6. Before creating/rotating the EasyAuth app credential on jumpbox, verify private Key Vault resolution and path:
  - `getent ahostsv4 "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name).vault.azure.net"`
  - If resolution is not private or access fails, rerun:
    - `./ops/scripts/phase2-network-dns.sh "${TARGET_ENV}" apply`
    - `./ops/scripts/phase3c-app-secrets.sh "${TARGET_ENV}" apply`
7. Then on the jumpbox, create or rotate the EasyAuth app credential and store it in the private app secrets Key Vault:
  - `sudo ./ops/scripts/configure-query-web-easyauth-secret.sh "${TARGET_ENV}" --secret-name "query-web-entra-client-secret-${TARGET_ENV}"`
8. Build and push immutable image tags from the jumpbox (only for images you are rolling out):
  ```bash
  sudo ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/build-push-ingestion.sh
  sudo ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/build-push-query-web.sh
  sudo ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/build-push-confluence-poller.sh
  ```
  Update the corresponding `*_image_tag` values in `infra/terraform/environments/${TARGET_ENV}/${TARGET_ENV}.tfvars` with the immutable tags produced above.
9. Roll out the standard agent hosting resources from jumpbox (non-RBAC app resources only):
  - `sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply --ingestion-tag "<immutable-ingestion-tag>" --query-web-tag "<immutable-query-web-tag>" --confluence-poller-tag "<immutable-confluence-poller-tag>" --enable-confluence-poller --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"`
10. After pushing a new query-web container, you may need to remap the web redirect url, with the command provided by the `rollout-agent-hosting.sh` if it is needed.
  - `az ad app update --id <app id GUID> --web-redirect-uris https://<container_name>.<location>.azurecontainerapps.io/.auth/login/aad/callback`

The standard private-network deployment path uses the Container App ingestion and query services. The `phase3b-agent-hosting.sh` script is only for the preview hosted-query-agent path and is not required for the normal runtime deployment.

Use a split operational model for standard private-network deployments:

- External/admin Entra bootstrap (app registration + reply URL when FQDN exists + runtime UAMI app ownership + Microsoft Graph `Application.ReadWrite.OwnedBy`): `./ops/scripts/rollout-query-web-entra.sh "${TARGET_ENV}" apply`
- Jumpbox rollout (non-RBAC app resources only): `sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply --ingestion-tag "<immutable-ingestion-tag>" --query-web-tag "<immutable-query-web-tag>" --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"`
- Admin RBAC reconciliation (privileged identity only, run from admin workstation/CI runner): `./ops/scripts/reconcile-rbac-admin.sh "${TARGET_ENV}" apply`

This avoids permission failures when jumpbox identities cannot manage role assignments.

Important context split:

- Run `rollout-agent-hosting.sh` on the jumpbox using the VM managed identity.
- Run `reconcile-rbac-admin.sh` from an admin context (for example your local admin shell or CI runner signed in with Owner/User Access Administrator permissions).
- Do not run `reconcile-rbac-admin.sh` from the jumpbox managed identity unless that identity has role-assignment write/delete privileges.

This should have already been taken care of by the configure-jumpbox.sh script, but if needing to reset the login:

```bash
az account clear
az login --identity
# If multiple UAMIs are attached, provide one explicitly:
# az login --identity --object-id "<agent-runtime-uami-object-id>"
```

### Deploy Ingestion Job Image

Build and push the ingestion image from a Docker-capable host inside the VNet, typically the jumpbox:

- `sudo ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/build-push-ingestion.sh`
- Update `ingestion_job_image_tag` in `infra/terraform/environments/<env>/<env>.tfvars` with `<immutable-ingestion-tag>` container tag

Roll out the new image tag from jumpbox with the standard non-RBAC rollout script:

```bash
sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "<immutable-ingestion-tag>"
```

**Important:** If the query-web container has already been deployed (and EasyAuth is configured), you must include the Entra secret arguments even when rolling out only the ingestion container, otherwise the web app's authentication configuration will be removed:

```bash
sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "<immutable-ingestion-tag>" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

**TODO:** Decouple ingestion and query-web container deployments so they can be rolled out independently without affecting each other's authentication or configuration state.

If RBAC resources need reconciliation after rollout, run:

```bash
# Run from admin context (not jumpbox UAMI context)
./ops/scripts/reconcile-rbac-admin.sh "${TARGET_ENV}" apply
```

After rollout, use the ingestion workflow described in [runtime/README.md](runtime/README.md) to upload files and start the Container App Job.

### Load Control Data

After the ingestion job has indexed evidence documents, load framework control requirements (for example Essential Eight, AESCSF, CIS Controls, ISM, NIST CSF, or PSPF) into the dedicated controls index.

Use the controls runner from inside the private network (jumpbox or CI runner) with the Search endpoint exported. The runner supports four modes:

- `parse` writes framework records to JSONL under `./parsed-controls`
- `publish` uploads an existing JSONL file into the controls index
- `parse-and-publish` performs both steps in one command
- `ensure-index` creates or updates the dedicated controls index without uploading records

Available framework parsers:

- `essential_eight`: ASD Essential Eight Maturity Model
- `aescsf`: Australian Energy Sector Cyber Security Framework (AESCSF v2 core workbook)
- `cis_controls`: CIS Controls v8 (local XLSX and PDF sourced by the operator)
- `ism`: ASD Information Security Manual (OSCAL catalog)
- `nist_csf`: NIST Cybersecurity Framework 2.0
- `pspf`: Australian Government Protective Security Policy Framework Release 2025 (public PSPF release PDF)

Use `--framework all` to parse or parse-and-publish all frameworks in one run, or pass one framework name to selectively load only that control set.

Parser outputs are written to `./parsed-controls` with framework-specific filenames, for example:

- `essential_eight_november-2023.jsonl`
- `aescsf_v2.jsonl`
- `cis_controls_v8.jsonl`
- `ism_latest.jsonl`
- `nist_csf_2-0.jsonl`
- `pspf_release_2025.jsonl`

```bash
TARGET_ENV="<env>"
TF_DIR="infra/terraform"

SEARCH_EP=$(terraform -chdir="${TF_DIR}" output -raw search_endpoint)
export AZURE_SEARCH_ENDPOINT="${SEARCH_EP}"

cd runtime
source .venv/bin/activate

# Parse a framework into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework aescsf

# Parse all frameworks into ./parsed-controls in one run
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework all

# Parse CIS Controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework cis_controls

# Parse ISM controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework ism

# Parse NIST CSF controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework nist_csf

# Parse PSPF controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework pspf

# Create or update the controls index only
python3 -m ingestion.controls_runner \
  --mode ensure-index

# Parse and publish in one step
python3 -m ingestion.controls_runner \
  --mode parse-and-publish \
  --framework essential_eight

# Publish an existing JSONL file directly
python3 -m ingestion.controls_runner \
  --mode publish \
  --input-jsonl ../parsed-controls/essential_eight_november-2023.jsonl

# Publish AESCSF JSONL directly
python3 -m ingestion.controls_runner \
  --mode publish \
  --input-jsonl ../parsed-controls/aescsf_v2.jsonl

# Publish CIS Controls JSONL directly
python3 -m ingestion.controls_runner \
  --mode publish \
  --input-jsonl ../parsed-controls/cis_controls_v8.jsonl
```

Add `--no-guidance` if you want parsers that support guidance-fetch skipping (for example Essential Eight and NIST CSF) to avoid supplementary guidance fetches while building JSONL output.

See [runtime/README.md](runtime/README.md) for the full controls pipeline reference, supported frameworks, runner options, and controls index environment variables.

### Deploy Query Web Image

Build and push the query web image from a Docker-capable host inside the VNet:

- `sudo ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/build-push-query-web.sh`
- Update `query_web_image_tag` in `infra/terraform/environments/<env>/<env>.tfvars` with `<immutable-query-web-tag>` container tag

Roll out the query web image from jumpbox:

```bash
sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --query-web-tag "<immutable-query-web-tag>" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

### Validate Query Web Deployment

Use the query web integration test runner from a private-network-connected host:

```bash
QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/run-query-web-integration-tests.sh "https://<query-web-fqdn>" "<optional-auth-token>"
```

See [runtime/README.md](runtime/README.md) for ingestion execution details and query endpoint usage.

## Common Commands

```bash
# Select environment
TARGET_ENV="<env>"

# Bootstrap and core infra
./ops/scripts/phase1-bootstrap.sh "${TARGET_ENV}"
./ops/scripts/phase2-network-dns.sh "${TARGET_ENV}" apply
./ops/scripts/phase3-data-ai.sh "${TARGET_ENV}" apply
./ops/scripts/phase3c-app-secrets.sh "${TARGET_ENV}" apply

# Build and push immutable images from a private-network-connected host
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-<gitsha>" ./ops/scripts/build-push-ingestion.sh
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-<gitsha>" ./ops/scripts/build-push-query-web.sh

# External/admin: create the Entra app registration used by query web EasyAuth
# and grant the least-privilege permission bundle needed for jumpbox credential rotation
./ops/scripts/rollout-query-web-entra.sh "${TARGET_ENV}" apply
# If UAMI auto-discovery fails, pass the object ID explicitly:
# ./ops/scripts/rollout-query-web-entra.sh "${TARGET_ENV}" apply --runtime-uami-principal-id "<uami-object-id>"

# Jumpbox: create/rotate EasyAuth app credential and publish to private Key Vault
sudo ./ops/scripts/configure-query-web-easyauth-secret.sh "${TARGET_ENV}" \
  --secret-name "query-web-entra-client-secret-${TARGET_ENV}"

# Roll out app image tags from jumpbox (non-RBAC resources)
sudo ./ops/scripts/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "<immutable-ingestion-tag>" \
  --query-web-tag "<immutable-query-web-tag>" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"

# Reconcile RBAC role assignments from admin context (local admin shell or CI)
# Do not run from jumpbox UAMI context unless that identity can manage role assignments.
./ops/scripts/reconcile-rbac-admin.sh "${TARGET_ENV}" apply

# Parse and publish control data into the controls index (run from inside private network)
SEARCH_EP=$(terraform -chdir=infra/terraform output -raw search_endpoint)
export AZURE_SEARCH_ENDPOINT="${SEARCH_EP}"
cd runtime && source .venv/bin/activate
python3 -m ingestion.controls_runner --mode parse-and-publish --framework aescsf
cd ..

# Run unit tests
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/unit -q

# Run query web integration tests from inside the private network
# N.B. This will fail with auth issues when the query form is secured by a security group and the script is run from the jumpbox.
QUERY_FQDN=$(terraform -chdir=infra/terraform output -raw query_web_fqdn)
QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/run-query-web-integration-tests.sh "https://${QUERY_FQDN}" "<optional-auth-token>"

# Quality tools

# Format
python -m black query_web runtime/assessment_orchestration runtime/ingestion tests --line-length 100
python -m isort query_web runtime/assessment_orchestration runtime/ingestion tests --line-length 100

python -m black --check query_web runtime/assessment_orchestration runtime/ingestion tests --line-length 100
python -m isort --check query_web runtime/assessment_orchestration runtime/ingestion tests --line-length 100

# Lint
python -m pylint query_web --disable=C0114,C0103,R0913,R0914,C0301,C0303 --max-line-length=100 --fail-under=8
python -m pylint runtime.assessment_orchestration --disable=C0114,C0103,R0913,R0914,C0301,C0303 --max-line-length=100 --fail-under=8
python -m pylint runtime.ingestion --disable=C0114,C0103,R0913,R0914,C0301,C0303 --max-line-length=100 --fail-under=8

# Type Check
python -m mypy query_web --ignore-missing-imports
python -m mypy runtime/assessment_orchestration --ignore-missing-imports
python -m mypy runtime/ingestion --ignore-missing-imports

# Test Coverage
pytest tests --cov-report=term-missing --cov=query_web --cov=runtime
```
