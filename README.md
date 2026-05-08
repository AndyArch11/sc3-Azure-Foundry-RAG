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

```
sc3-Azure-Foundry-RAG/
│
├── .github/
│   └── workflows/                  # GitHub Actions CI/CD pipelines
│
├── .agents/
│   └── skills/                     # Copilot agent skill definitions (SKILL.md files)
│
├── docs/                           # Architecture, plans, ADRs, and runbooks
│   ├── adr/                        # Architecture decision records
│   ├── contracts/                  # MCP tool and provider event YAML contracts
│   └── *.md                        # Implementation plans, observability, phase guides, etc.
│
├── infra/
│   └── terraform/
│       ├── aws/                    # AWS Terraform stack
│       │   ├── bootstrap/          # Phase 0: S3 state bucket + DynamoDB lock table
│       │   ├── environments/       # Per-environment tfvars (dev / test / prod)
│       │   └── modules/
│       │       ├── app_hosting/    # ECS Fargate cluster, task definitions, services
│       │       ├── app_secrets/    # Secrets Manager runtime secrets
│       │       ├── container_registry/ # ECR repositories
│       │       ├── data_services/  # S3, OpenSearch Service, DynamoDB
│       │       ├── identity/       # ECS IAM task role + execution role
│       │       ├── network/        # VPC, subnets, NAT gateways, VPC endpoints
│       │       └── observability/  # CloudWatch log groups, AMP workspace
│       └── azure/                  # Azure Terraform stack (canonical)
│           ├── bootstrap/          # Phase 0: storage account + Key Vault for tf state
│           ├── environments/       # Per-environment tfvars (dev / test / prod)
│           └── modules/
│               ├── agent_hosting/  # Container Apps environment, ingestion job, query-web app
│               ├── app_secrets/    # Key Vault for runtime secrets
│               ├── bastion_jumpbox/# Azure Bastion host + jumpbox VM
│               ├── data_services/  # Storage account, AI Search, Cosmos DB, ACR
│               ├── dns/            # Private DNS zones and VNet links
│               ├── foundation/     # Resource group
│               ├── foundry/        # Azure AI Foundry hub, project, model deployments
│               ├── identity/       # User-assigned managed identity + RBAC assignments
│               ├── network/        # VNet, subnets, NSGs
│               ├── observability/  # Log Analytics Workspace, diagnostic settings
│               └── private_endpoints/ # Private endpoints for all data plane services
│
├── ops/
│   ├── ci/                         # Self-hosted CI runner setup notes
│   ├── containers/
│   │   └── terraform-runner/       # Deterministic Terraform execution container
│   ├── observability/              # Local Prometheus, Grafana, Loki, Alertmanager configs
│   └── scripts/
│       ├── aws/                    # AWS bootstrap, image build/push, app rollout scripts
│       ├── azure/                  # Azure bootstrap, image build/push, jumpbox, rollout scripts
│       └── local/                  # Local dev helpers (Terraform install, Qdrant seeder, smoke test)
│
├── parsed-controls/                # Generated JSONL control data (git-ignored; created by controls_runner)
│
├── query_web/                      # Query web application (FastAPI, deployed to Container Apps / ECS)
│   ├── endpoints/                  # Per-route modules: ask, compliance, conversations, corpus, etc.
│   ├── pipeline/                   # RAG pipeline: search, LLM chat, answer assembly, control retrieval
│   ├── security/                   # Auth middleware, prompt injection guard
│   ├── policies/                   # Precedence policy JSON
│   ├── static/                     # CSS, JS, branding assets
│   ├── templates/                  # Jinja2 HTML templates
│   ├── app.py                      # FastAPI application entry point
│   ├── config.py                   # Environment-driven configuration loader
│   ├── Dockerfile                  # Container image definition
│   └── requirements.txt
│
├── runtime/                        # Ingestion runtime and assessment orchestration
│   ├── assessment_orchestration/   # Agent orchestration: worker, poller, MCP clients, LLM factory
│   │   └── mcp/                    # MCP client adapters (Confluence, SharePoint, email, Azure resource)
│   ├── credentials/                # Cloud credential factory (Azure, AWS, local)
│   ├── ingestion/                  # Chunking, extraction, controls runner, search pipeline
│   │   └── parsers/                # Framework-specific control parsers (Essential Eight, ISM, NIST, etc.)
│   ├── llm/                        # LLM provider adapters: Azure OpenAI, Bedrock, Ollama (protocol-based)
│   ├── samples/                    # Sample input files for local development
│   │   └── api/
│   │       ├── corpus-a/           # Corpus A parser source references
│   │       ├── corpus-b/           # Corpus B narrative grounding source files
│   │       └── corpus-c/           # Corpus C evidence files for local chunk generation
│   ├── search/                     # Search provider adapters: Azure AI Search, OpenSearch, Qdrant, in-memory
│   ├── state_store/                # State store abstraction (Cosmos DB, DynamoDB, SQLite)
│   ├── storage/                    # Blob storage abstraction (Azure Blob, S3, local file)
│   ├── Dockerfile                  # Ingestion job container
│   ├── Dockerfile.poller           # Confluence poller container
│   └── requirements.txt
│
└── tests/
    ├── unit/                       # Fast unit tests (no network, no cloud dependencies)
    ├── integration/                # Private-network integration tests (run from jumpbox / CI runner)
    ├── smoke/                      # Lightweight smoke tests (AWS infrastructure, local stack)
    └── evals/                      # Skill selection evaluation cases and schemas
```

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

1. Set `TARGET_ENV` and update `infra/terraform/azure/environments/<env>/<env>.tfvars`.
2. Run `./ops/scripts/azure/phase1-bootstrap.sh <env>`, `./ops/scripts/azure/phase2-network-dns.sh <env> apply`, and `./ops/scripts/azure/phase3-data-ai.sh <env> apply`.
3. Build and push immutable ingestion and query image tags from a private-network-connected host.
4. Roll out those image tags through Terraform against `module.agent_hosting`.
5. Upload or ingest source documents, start the ingestion job, and load control data (for example Essential Eight) into the controls index.
6. Run `./ops/scripts/azure/run-query-web-integration-tests.sh "https://<query-web-fqdn>" "<optional-auth-token>"` from inside the private network.

## Quick Start

1. Build the Terraform runner image:
   - `docker build -t tf-runner:local ops/containers/terraform-runner`
2. Or, if Docker is unavailable in your working environment, install Terraform locally:
   - `./ops/scripts/local/install-terraform-local.sh`
3. Choose an environment and update its tfvars file:
   - `infra/terraform/azure/environments/<env>/<env>.tfvars`
4. Bootstrap remote state and supporting secrets:
   - `./ops/scripts/azure/phase1-bootstrap.sh <env>`
5. Apply the core platform phases:
   - `./ops/scripts/azure/phase2-network-dns.sh <env> apply`
   - `./ops/scripts/azure/phase3-data-ai.sh <env> apply`
6. Build, push, and roll out runtime images as described in the Deployment runbook below.

## Documentation Index

- Detailed implementation sequencing: `docs/implementation-plan.md`
- Delivery slicing by phase: `docs/phases.md`
- Testing policy and private-endpoint test execution: `docs/testing-strategy.md`
- Logging and metrics baseline: `docs/observability.md`
- Foundry setup and deployment prerequisites: `docs/foundry-setup-guide.md`
- Conversation persistence and feedback flow: `docs/foundry-conversations.md`
- AWS deployment runbook (service mapping, bootstrap, rollout, teardown): `docs/aws-deployment-guide.md`

## Local Development

The query web application runs fully offline against local JSONL data, Ollama, and Qdrant — no Azure subscription required.

### Prerequisites

- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- Python virtual environment with `requirements-dev.txt` installed
- (Optional) Qdrant for vector search — otherwise in-memory search is used

### Generating evidence data

`runtime/out/` is not committed to the repository. It is created on demand by the ingestion runner. Run this once from the `runtime/` directory to generate `runtime/out/chunks.jsonl` before starting the local stack:

```bash
cd runtime
source .venv/bin/activate
python -m ingestion.runner --mode local --input-dir ./samples/api/corpus-c --output-jsonl ./out/chunks.jsonl
cd ..
```

For clean Corpus C local evidence, keep framework source reference files (for example CIS/PCI/AESCSF control parser inputs) out of the local evidence input directory. If framework source files are present in `runtime/samples/api/corpus-a`, they should not be included in Corpus C chunk generation.

Recommended local split:
- `runtime/samples/api/corpus-a/` for Corpus A parser source references
- `runtime/samples/api/corpus-b/` for local Corpus B narrative grounding source files
- `runtime/samples/api/corpus-c/` for Corpus C evidence files used by local chunk generation

Corpus B is grounding data that supports Corpus A grounding and should be managed separately from Corpus C evidence sources.

If no evidence files are available yet, the app and seeder will start with an empty evidence index and log a warning — queries will simply return no grounding documents until the index is populated.

Control data (`parsed-controls/`) is not committed to the repository and must be generated before starting the local stack. Run the controls runner once from the repo root:

```bash
cd runtime
source .venv/bin/activate

# Generate all frameworks that fetch their source data automatically
python3 -m ingestion.controls_runner --mode parse --framework essential_eight
python3 -m ingestion.controls_runner --mode parse --framework aescsf
python3 -m ingestion.controls_runner --mode parse --framework ism
python3 -m ingestion.controls_runner --mode parse --framework nist_csf
python3 -m ingestion.controls_runner --mode parse --framework nist_ai_rmf
python3 -m ingestion.controls_runner --mode parse --framework pspf

cd ..
```

> **Note:** CIS Controls and PCI DSS cannot be fetched automatically due to licensing restrictions. They require operator-supplied source files staged in `runtime/samples/api/corpus-a/` before parsing:
> - CIS Controls: stage the CIS Controls v8 XLSX and PDF files (see [runtime/README.md](runtime/README.md) for required filenames)
> - PCI DSS: stage `PCI-DSS-v4_0_1.pdf` in `runtime/samples/api/corpus-a/`
>
> Once staged, run `python3 -m ingestion.controls_runner --mode parse --framework cis_controls` and `python3 -m ingestion.controls_runner --mode parse --framework pci_dss` respectively.

If no control files are available yet, the app will start with an empty controls index — compliance queries will return no grounding documents until the index is populated.

### Option A — In-memory search (fastest, IDE-friendly)

No Qdrant or seeding step needed. JSONL files load directly into memory at app startup.

```bash
# Install dependencies
sudo python3 -m venv runtime/.venv
source runtime/.venv/bin/activate
python3 -m pip install -r requirements-dev.txt

# Pull Ollama models once
ollama pull nomic-embed-text
ollama pull gemma3:27b

# Set environment and start the app
CLOUD_PROVIDER=local \
LOCAL_VECTOR_BACKEND=inmemory \
LOCAL_EVIDENCE_JSONL_PATH=./runtime/out/chunks.jsonl \
LOCAL_CONTROLS_JSONL_PATH=./parsed-controls \
PRECEDENCE_POLICY_PATH=./query_web/policies/precedence_policy.json \
OLLAMA_BASE_URL=http://host.docker.internal:11434  \
OLLAMA_MODEL=gemma3:27b \
QUERY_WEB_AUTH_TOKEN='' \
uvicorn query_web.app:app --host 0.0.0.0 --port 8080 --reload
```

Open [http://localhost:8080](http://localhost:8080).

> **Dev container / WSL users:** uvicorn must bind to `0.0.0.0` (included above) to be reachable from the Windows host. In VS Code, use the **Ports** panel and open the **Forwarded Address** for the active (green) forwarded entry. The host port is often remapped (for example container port `8080` may appear as `127.0.0.1:44500`), so do not assume `localhost:8080`. If no forwarded entry appears, use the Command Palette → **Forward a Port** → `8080`.

### Option B — Qdrant vector search (higher-fidelity local retrieval)

Requires Qdrant running and a one-time seeding step to embed and index documents.

```bash
# Start Qdrant
docker run -d -p 6333:6333 --name rag-qdrant qdrant/qdrant

# Seed indexes (embed JSONL via Ollama and upsert into Qdrant)
CLOUD_PROVIDER=local \
LOCAL_EVIDENCE_JSONL_PATH=./runtime/out/chunks.jsonl \
LOCAL_CONTROLS_JSONL_PATH=./parsed-controls \
QDRANT_URL=http://host.docker.internal:6333 \
OLLAMA_BASE_URL=http://host.docker.internal:11434 \
python ops/scripts/local/seed_local.py

# Check indexed counts
LOCAL_EVIDENCE_JSONL_PATH=./runtime/out/chunks.jsonl \
LOCAL_CONTROLS_JSONL_PATH=./parsed-controls \
QDRANT_URL=http://host.docker.internal:6333 \
OLLAMA_BASE_URL=http://host.docker.internal:11434 \
python ops/scripts/local/seed_local.py --check

# Start the app
CLOUD_PROVIDER=local \
LOCAL_VECTOR_BACKEND=qdrant \
QDRANT_URL=http://host.docker.internal:6333 \
PRECEDENCE_POLICY_PATH=./query_web/policies/precedence_policy.json \
OLLAMA_BASE_URL=http://host.docker.internal:11434 \
OLLAMA_MODEL=gemma3:27b \
QUERY_WEB_AUTH_TOKEN='' \
uvicorn query_web.app:app --host 0.0.0.0 --port 8080 --reload
```

Seeder options: `--force` (re-seed even if populated), `--evidence-only`, `--controls-only`.

### Option C — Self-contained Docker Compose stack

Runs Ollama, Qdrant, the seeder, and query-web together with correct startup ordering.

This path avoids host bind mounts for `runtime/out` and `parsed-controls`.
Compose seeds those datasets into Docker named volumes via a one-off `local-data-init` service,
which is resilient in devcontainer/WSL setups where `/workspaces/...` bind mounts can appear empty.

```bash
# Copy and customise environment
cp .env.local.example .env.local

# If Windows already uses localhost:8080, set a different host port
# e.g. QUERY_WEB_HOST_PORT=18080

# Build and start (model pull + seeding run automatically before query-web starts)
# N.B. this can take up to 20 minutes or more for all containers to start
docker compose -f docker-compose.local.yml --env-file .env.local up --build

# Run local Ask end-to-end smoke test
QUERY_WEB_BASE_URL=http://host.docker.internal:${QUERY_WEB_HOST_PORT:-8080} ./ops/scripts/local/smoke-local-ask.sh
```

When local evidence or controls source data changes, rebuild and force the init service once to refresh volume contents:

```bash
docker compose -f docker-compose.local.yml --env-file .env.local up --build --force-recreate local-data-init
```

Model names (`OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL`) are read from `.env.local` and used consistently by the model-puller, seeder, and query-web services. Change them in one place only.

`OLLAMA_EMBED_MAX_CHARS` (default `6000`) caps the number of characters sent per embedding request. If Ollama returns a context-length error the input is halved automatically until it fits; lower this value if your embedding model has a smaller context window.

To run the local Confluence poller continuously (and populate **Assess → Confluence Poll Activity**), enable the optional compose profile after setting Confluence credentials in `.env.local`:

```bash
docker compose -f docker-compose.local.yml --env-file .env.local --profile confluence-poller up --build
```

The poller and query-web share `LOCAL_STATE_DB_PATH` (`/app/local_state/state.db` in compose), so poll summaries and assessed-page status written by the poller are visible in the Assess tab.

Open [http://localhost:${QUERY_WEB_HOST_PORT:-8080}](http://localhost:${QUERY_WEB_HOST_PORT:-8080}).

> **Guidance on backends:**  
> Use `inmemory` for fast iteration on code (no seeding step, instant startup).  
> Use `qdrant` when testing query quality or debugging retrieval behaviour.


### Option D — Local Observability Stack (Prometheus + Grafana + Loki)

Run this on top of the local compose stack to scrape `query-web`, auto-provision Grafana datasources, and query local Python stdout via Loki. This is a local-only convenience path; Azure and AWS environments should continue using cloud-native logging sinks.

Prometheus, Alertmanager, Loki, and Promtail local configs are generated at container startup (under `/tmp`) so this profile also avoids fragile `/workspaces/...` bind-mounted config paths.

```bash
# Start app stack + observability profile together
docker compose \
  -f docker-compose.local.yml \
  -f docker-compose.observability.yml \
  --env-file .env.local \
  --profile observability \
  up --build
```

Endpoints:
- Query web: [http://localhost:${QUERY_WEB_HOST_PORT:-8080}](http://localhost:${QUERY_WEB_HOST_PORT:-8080})
- Prometheus: [http://localhost:9090](http://localhost:9090)
- Grafana: [http://localhost:3000](http://localhost:3000)
- Loki: [http://localhost:3100](http://localhost:3100)
- Alertmanager: [http://localhost:9093](http://localhost:9093)
- Webhook echo (alert sink): [http://localhost:8090](http://localhost:8090)

Grafana starts with anonymous admin enabled and auto-loads the dashboard **Query Web Local Observability**. It also provisions a Loki datasource for local log exploration, and the dashboard now includes a built-in **Recent Query Web Logs** panel. Promtail scrapes Docker stdout for the local Python services (`query-web` and `confluence-poller`, when enabled) and forwards those logs into Loki. Alertmanager receives fired alerts from Prometheus and delivers them to the `webhook-echo` container, whose logs show the full JSON payload.

Quick validation after startup:

```bash
# Set this if you're running commands from a dev container/WSL shell
# QUERY_WEB_BASE_URL=http://host.docker.internal:${QUERY_WEB_HOST_PORT:-8080}
QUERY_WEB_BASE_URL=${QUERY_WEB_BASE_URL:-http://localhost:${QUERY_WEB_HOST_PORT:-8080}}

# Confirm scrape endpoint is exposed by query-web
curl -s "${QUERY_WEB_BASE_URL}/metrics" | grep -E "http_request_duration_seconds|trace_traceparent_dropped_total|trace_correlation_id_sanitised_total"

# Confirm Prometheus sees query-web target up
curl -s http://localhost:9090/api/v1/targets | grep -E '"health":"up"|query-web'

# Confirm Loki is reachable
curl -s http://localhost:3100/ready

# Confirm Promtail is discovering local Python containers
docker logs rag-promtail-local --tail 40

# Confirm alerting rules are loaded
curl -s http://localhost:9090/api/v1/rules | grep -E 'QueryWebHighP95Latency|QueryWebHighP99Latency|QueryWebTraceparentDropSpike|QueryWebCorrelationIdSanitisationSpike|QueryWebTargetDown|QueryWebNoTraffic'

# Check currently active alerts
curl -s http://localhost:9090/api/v1/alerts

# Confirm Alertmanager is healthy
curl -s http://localhost:9093/-/healthy

# See alerts received by the webhook echo sink
docker logs rag-webhook-echo-local --tail 40
```

In Grafana Explore, start with a Loki query like:

```logql
{service="query-web"} | json | __error__ = "" | correlation_id != ""
```

Or for the optional local poller:

```logql
{service="confluence-poller"} | json | __error__ = "" | level = "WARNING"
```

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
3. Use the environment-specific tfvars under `infra/terraform/azure/environments/${TARGET_ENV}/` as the source of truth.
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
  - `./ops/scripts/azure/l-terraform-local.sh`

Login to Azure
- `az login`

Deployment assumes that a target Azure subscription has already been created. Select target subscription to set target context for scripts.
- `az account set --subscription "target-subscription-name"`

Run the environment build scripts in order (can take over 1 hour to provision the Azure resources):

1. Create Azure resources required to support Terraform:
  - `./ops/scripts/azure/e1-bootstrap.sh "${TARGET_ENV}"`
2. Create Azure resources required to secure solution by private network (not required if bringing your own network, run phase 3 instead):
  - `./ops/scripts/azure/phase2-network-dns.sh "${TARGET_ENV}" apply`
3. Optional Create Foundry related Azure resources (only required if BYOL network resources or wanting the jumpbox/bastion host):
  - `./ops/scripts/azure/phase3-data-ai.sh "${TARGET_ENV}" apply`
4. Create private app secrets Key Vault and private endpoint:
  - `./ops/scripts/azure/phase3c-app-secrets.sh "${TARGET_ENV}" apply`
5. Optional preview-only hosted agent path (ignore this step unless wanting to play with hosted agents - untested code): 
  - `ENABLE_HOSTED_QUERY_AGENT_PREVIEW=true ./ops/scripts/azure/phase3b-agent-hosting.sh "${TARGET_ENV}" apply`

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
  - `sudo ./ops/scripts/azure/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --init-terraform-backend "${TARGET_ENV}" --run-unit-tests`
4. b) If the VM has multiple user-assigned identities, pass the intended client ID explicitly:
  - `sudo ./ops/scripts/azure/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --az-login-client-id "<agent-runtime-uami-client-id>" --init-terraform-backend "${TARGET_ENV}" --run-unit-tests`
5. If using Entra group-gated query web auth, run the external/admin Entra bootstrap first to create the query web app registration target:
  - `./ops/scripts/azure/rollout-query-web-entra.sh "${TARGET_ENV}" apply`
6. Before creating/rotating the EasyAuth app credential on jumpbox, verify private Key Vault resolution and path:
  - `getent ahostsv4 "$(terraform -chdir=infra/terraform/azure output -raw app_secrets_key_vault_name).vault.azure.net"`
  - If resolution is not private or access fails, rerun:
    - `./ops/scripts/azure/phase2-network-dns.sh "${TARGET_ENV}" apply`
    - `./ops/scripts/azure/phase3c-app-secrets.sh "${TARGET_ENV}" apply`
7. Then on the jumpbox, create or rotate the EasyAuth app credential and store it in the private app secrets Key Vault:
  - `sudo ./ops/scripts/azure/configure-query-web-easyauth-secret.sh "${TARGET_ENV}" --secret-name "query-web-entra-client-secret-${TARGET_ENV}"`
8. Build and push immutable image tags from the jumpbox (only for images you are rolling out):
  ```bash
  ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-ingestion.sh
  ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-query-web.sh
  ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-confluence-poller.sh
  ```
  - Update the corresponding `*_image_tag` values in `infra/terraform/azure/environments/${TARGET_ENV}/${TARGET_ENV}.tfvars` with the immutable tags produced above.
  - After creating a new container image, you may want to run the optional RBAC reconcilliation command (requires elevated privileges): 
    - `./ops/scripts/azure/reconcile-rbac-admin.sh ${TARGET_ENV} apply`
9. Roll out the standard agent hosting resources from jumpbox (non-RBAC app resources only):
  - `sudo ./ops/scripts/azure/rollout-agent-hosting.sh "${TARGET_ENV}" apply --ingestion-tag "<immutable-ingestion-tag>" --query-web-tag "<immutable-query-web-tag>" --confluence-poller-tag "<immutable-confluence-poller-tag>" --enable-confluence-poller --entra-secret-kv "$(terraform -chdir=infra/terraform/azure output -raw app_secrets_key_vault_name)" --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"`
10. After pushing a new query-web container, you may need to remap the web redirect url, with the command provided by the `rollout-agent-hosting.sh` if it is needed (requires elevated privileges).
  - `az ad app update --id <app id GUID> --web-redirect-uris https://<container_name>.<location>.azurecontainerapps.io/.auth/login/aad/callback`

The standard private-network deployment path uses the Container App ingestion and query services. The `phase3b-agent-hosting.sh` script is only for the preview hosted-query-agent path and is not required for the normal runtime deployment.

Use a split operational model for standard private-network deployments:

- External/admin Entra bootstrap (app registration + reply URL when FQDN exists + runtime UAMI app ownership + Microsoft Graph `Application.ReadWrite.OwnedBy`): `./ops/scripts/azure/rollout-query-web-entra.sh "${TARGET_ENV}" apply`
- Jumpbox rollout (non-RBAC app resources only): `sudo ./ops/scripts/azure/rollout-agent-hosting.sh "${TARGET_ENV}" apply --ingestion-tag "<immutable-ingestion-tag>" --query-web-tag "<immutable-query-web-tag>" --entra-secret-kv "$(terraform -chdir=infra/terraform/azure output -raw app_secrets_key_vault_name)" --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"`
- Admin RBAC reconciliation (privileged identity only, run from admin workstation/CI runner): `./ops/scripts/azure/reconcile-rbac-admin.sh "${TARGET_ENV}" apply`

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

- `ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-ingestion.sh`
- Update `ingestion_job_image_tag` in `infra/terraform/azure/environments/<env>/<env>.tfvars` with `<immutable-ingestion-tag>` container tag

Roll out the new image tag from jumpbox with the standard non-RBAC rollout script:

```bash
sudo ./ops/scripts/azure/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "<immutable-ingestion-tag>"
```

**Important:** If the query-web container has already been deployed (and EasyAuth is configured), you must include the Entra secret arguments even when rolling out only the ingestion container, otherwise the web app's authentication configuration will be removed:

```bash
sudo ./ops/scripts/azure/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "<immutable-ingestion-tag>" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform/azure output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

**TODO:** Decouple ingestion, poller, and query-web container deployments so they can be rolled out independently without affecting each other's authentication or configuration state.

If RBAC resources need reconciliation after rollout, run:

```bash
# Run from admin context (not jumpbox UAMI context)
./ops/scripts/azure/reconcile-rbac-admin.sh "${TARGET_ENV}" apply
```

After rollout, use the ingestion workflow described in [runtime/README.md](runtime/README.md) to upload files and start the Container App Job.


### Load Control Data

After the ingestion job has indexed evidence documents, load framework control requirements (for example Essential Eight, AESCSF, CIS Controls, ISM, NIST CSF, PCI DSS, or PSPF) into the dedicated controls index.

Use the controls runner from inside the private network (jumpbox or CI runner) with the Search endpoint exported. The runner supports four modes:

- `parse` writes framework records to JSONL under `./parsed-controls`
- `publish` uploads an existing JSONL file into the controls index
- `parse-and-publish` performs both steps in one command
- `ensure-index` creates or updates the dedicated controls index without uploading records

Available framework parsers:

- `essential_eight`: ASD Essential Eight Maturity Model
- `aescsf`: Australian Energy Sector Cyber Security Framework (AESCSF v2 core workbook)
- `cis_controls`: CIS Controls v8 (local XLSX and PDF sourced by the operator — see [runtime/README.md](runtime/README.md) for required filenames)
- `ism`: ASD Information Security Manual (OSCAL catalog)
- `nist_ai_rmf`: NIST AI Risk Management Framework 1.0 (auto-fetched from NIST source PDF)
- `nist_csf`: NIST Cybersecurity Framework 2.0
- `pci_dss`: PCI DSS v4.0.1 (local PDF sourced by the operator — see [runtime/README.md](runtime/README.md) for required filename)
- `pspf`: Australian Government Protective Security Policy Framework Release 2025 (public PSPF release PDF)

Use `--framework all` to parse or parse-and-publish all frameworks in one run, or pass one framework name to selectively load only that control set.

Parser outputs are written to `./parsed-controls` with framework-specific filenames, for example:

- `essential_eight_november-2023.jsonl`
- `aescsf_v2.jsonl`
- `cis_controls_v8.jsonl`
- `ism_latest.jsonl`
- `nist_ai_rmf_1-0.jsonl`
- `nist_csf_2-0.jsonl`
- `pci_dss_v4_0_1.jsonl`
- `pspf_release_2025.jsonl`

```bash
TARGET_ENV="<env>"
TF_DIR="infra/terraform/azure"

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
# Requires CIS documents staged in runtime/samples/api/corpus-a/ first
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework cis_controls

# Parse ISM controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework ism

# Parse NIST AI RMF controls into ./parsed-controls only
# Source PDF is fetched automatically from NIST
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework nist_ai_rmf

# Parse NIST CSF controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework nist_csf

# Parse PSPF controls into ./parsed-controls only
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework pspf

# Parse PCI DSS controls into ./parsed-controls only
# Requires PCI-DSS-v4_0_1.pdf staged in runtime/samples/api/corpus-a/ first
python3 -m ingestion.controls_runner \
  --mode parse \
  --framework pci_dss

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

# Parse and publish PCI DSS in one step
# Requires PCI-DSS-v4_0_1.pdf staged in runtime/samples/api/corpus-a/ first
python3 -m ingestion.controls_runner \
  --mode parse-and-publish \
  --framework pci_dss

# Publish PCI DSS JSONL directly
python3 -m ingestion.controls_runner \
  --mode publish \
  --input-jsonl ../parsed-controls/pci_dss_v4_0_1.jsonl

# Parse and publish NIST AI RMF in one step
# Source PDF is fetched automatically from NIST
python3 -m ingestion.controls_runner \
  --mode parse-and-publish \
  --framework nist_ai_rmf

# Publish NIST AI RMF JSONL directly
python3 -m ingestion.controls_runner \
  --mode publish \
  --input-jsonl ../parsed-controls/nist_ai_rmf_1-0.jsonl
```

Add `--no-guidance` if you want parsers that support guidance-fetch skipping (for example Essential Eight and NIST CSF) to avoid supplementary guidance fetches while building JSONL output.

See [runtime/README.md](runtime/README.md) for the full controls pipeline reference, supported frameworks, runner options, and controls index environment variables.

### Deploy Query Web Image

Build and push the query web image from a Docker-capable host inside the VNet:

- `ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-query-web.sh`
- Update `query_web_image_tag` in `infra/terraform/azure/environments/<env>/<env>.tfvars` with `<immutable-query-web-tag>` container tag

Roll out the query web image from jumpbox:

```bash
sudo ./ops/scripts/azure/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --query-web-tag "<immutable-query-web-tag>" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform/azure output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}"
```

### Validate Query Web Deployment

Use the query web integration test runner from a private-network-connected host:

```bash
QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/azure/run-query-web-integration-tests.sh "https://<query-web-fqdn>" "<optional-auth-token>"
```

See [runtime/README.md](runtime/README.md) for ingestion execution details and query endpoint usage.

### Uninstall from Azure

If not already initialised in shell/session, run init first:

`terraform -chdir=infra/terraform/azure init -backend-config=infra/terraform/azure/environments/dev/backend.hcl`

For the Azure dev platform stack, run:

`terraform -chdir=infra/terraform/azure destroy -input=false -var-file=environments/dev/bootstrap.generated.tfvars -var-file=environments/dev/dev.tfvars`

After platform destroy completes, destroy bootstrap (if you want full teardown including state backend resources):

`terraform -chdir=infra/terraform/azure/bootstrap destroy -input=false -var-file=terraform.tfvars`

## Common Commands

```bash
# Select environment
TARGET_ENV="<env>"

#Azure auth
az login --identity

# Bootstrap and core infra
./ops/scripts/azure/phase1-bootstrap.sh "${TARGET_ENV}"
./ops/scripts/azure/phase2-network-dns.sh "${TARGET_ENV}" apply
./ops/scripts/azure/phase3-data-ai.sh "${TARGET_ENV}" apply
./ops/scripts/azure/phase3c-app-secrets.sh "${TARGET_ENV}" apply

# Build and push immutable images from a private-network-connected host
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-ingestion.sh
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-query-web.sh
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git -C . rev-parse --short HEAD)" ./ops/scripts/azure/build-push-confluence-poller.sh

# External/admin: create the Entra app registration used by query web EasyAuth
# and grant the least-privilege permission bundle needed for jumpbox credential rotation
./ops/scripts/azure/rollout-query-web-entra.sh "${TARGET_ENV}" apply
# If UAMI auto-discovery fails, pass the object ID explicitly:
# ./ops/scripts/azure/rollout-query-web-entra.sh "${TARGET_ENV}" apply --runtime-uami-principal-id "<uami-object-id>"

# Jumpbox: create/rotate EasyAuth app credential and publish to private Key Vault
sudo ./ops/scripts/azure/configure-query-web-easyauth-secret.sh "${TARGET_ENV}" \
  --secret-name "query-web-entra-client-secret-${TARGET_ENV}"

# Roll out app image tags from jumpbox (non-RBAC resources)
# Confluence args only required if not in env.tfvars file or set in environment variables
sudo ./ops/scripts/azure/rollout-agent-hosting.sh "${TARGET_ENV}" apply \
  --ingestion-tag "<immutable-ingestion-tag>" \
  --query-web-tag "<immutable-query-web-tag>" \
  --entra-secret-kv "$(terraform -chdir=infra/terraform/azure output -raw app_secrets_key_vault_name)" \
  --entra-secret-name "query-web-entra-client-secret-${TARGET_ENV}" \
  --confluence-poller-tag "<immutable-confluence-poller-tag>" \
  --confluence-base-url "https://<org>.atlassian.net" \
  --confluence-auth-mode basic \
  --confluence-auth-email "<service_account@emaildomain.com>" \
  --confluence-cloud-id "<00000000-0000-0000-0000-000000000000>" \
  --confluence-api-token "<base64token>"

# Reconcile RBAC role assignments from admin context (local admin shell or CI)
# Do not run from jumpbox UAMI context unless that identity can manage role assignments.
./ops/scripts/azure/reconcile-rbac-admin.sh "${TARGET_ENV}" apply

# Register query-web app auth callback for RBAC group membership access
# Needs to be run with external/admin privileges
az ad app update --id <00000000-0000-0000-0000-000000000000> --web-redirect-uris https://<webapp>.australiaeast.azurecontainerapps.io/.auth/login/aad/callback

# Parse and publish control data into the controls index (run from inside private network)
# Supported frameworks: all, aescsf, cis_controls, essential_eight, ism, nist_ai_rmf, nist_csf, pci_dss, pspf
# cis_controls and pci_dss require staging local reference files in runtime/samples/api/corpus-a/ first
SEARCH_EP=$(terraform -chdir=infra/terraform/azure output -raw search_endpoint)
export AZURE_SEARCH_ENDPOINT="${SEARCH_EP}"
cd runtime && source .venv/bin/activate
python3 -m ingestion.controls_runner --mode parse-and-publish --framework aescsf
cd ..

# Run unit tests
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/unit -q

# Run query web integration tests from inside the private network
# N.B. This will fail with auth issues when the query form is secured by a security group and the script is run from the jumpbox.
QUERY_FQDN=$(terraform -chdir=infra/terraform/azure output -raw query_web_fqdn)
QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/azure/run-query-web-integration-tests.sh "https://${QUERY_FQDN}" "<optional-auth-token>"

# Quality tools

# Format
python3 -m isort query_web runtime/assessment_orchestration runtime/ingestion tests
python3 -m black query_web runtime/assessment_orchestration runtime/ingestion tests

python3 -m isort --check query_web runtime/assessment_orchestration runtime/ingestion tests
python3 -m black --check query_web runtime/assessment_orchestration runtime/ingestion tests

# Lint
python3 -m pylint query_web --disable=C0114,C0103,R0913,R0914,C0301,C0303 --max-line-length=100 --fail-under=8
python3 -m pylint runtime.assessment_orchestration --disable=C0114,C0103,R0913,R0914,C0301,C0303 --max-line-length=100 --fail-under=8
python3 -m pylint runtime.ingestion --disable=C0114,C0103,R0913,R0914,C0301,C0303 --max-line-length=100 --fail-under=8

# Type Check
python3 -m mypy query_web --ignore-missing-imports
python3 -m mypy runtime/assessment_orchestration --ignore-missing-imports
python3 -m mypy runtime/ingestion --ignore-missing-imports

# Test Coverage
python3 -m pytest tests --cov-report=term-missing --cov=query_web --cov=runtime
```
