# Troubleshooting Guide

## Before You Start

**Administrative actions** (Terraform, ACR, Key Vault management, role assignments) use your personal Azure identity via `az login`.

**Network-secured actions** (Container App log streaming, blob access via private endpoint, internal service calls) must be performed from the jumpbox using `az login --identity`.

Always set your target environment first:

```bash
export TARGET_ENV="dev"   # or prod, staging, etc.
```

Make sure you are in the project root and on the latest code:

```bash
cd sc3-Azure-Foundry-RAG
git pull
```

Activate the Python virtual environment before running any Python commands:

```bash
source runtime/.venv/bin/activate
```

---

## Solution Components

The deployment consists of:

- **GitHub** — source and CI/CD
- **Terraform** — infrastructure provisioning
- **Azure Container Registry (ACR)** — image storage
- **Azure Container Apps** — query web app, ingestion job, Confluence poller
- **Azure Key Vault** — secrets
- **Azure Storage** — blob storage for corpus data and Terraform state
- **Azure Cosmos DB** — conversation history and orchestration state
- **Azure Foundry / Azure OpenAI** — LLM inference and embeddings
- **Azure AI Search** — grounding index
- **Private VNet + private endpoints** — network isolation

Most runtime actions require both Azure identity and jumpbox network access.

---

## Common Quick Checks

## AWS-Specific Troubleshooting Tips

Use these checks when running the AWS deployment (`infra/terraform/aws`).

### 1) Bedrock calls always return fallback or `insufficient_evidence`

If compliance output repeatedly falls back to deterministic `insufficient_evidence`, check query-web logs for hidden Bedrock errors:

```bash
aws logs filter-log-events \
  --log-group-name "/ecs/rag-dev-apse2/query-web" \
  --region ap-southeast-2 \
  --start-time $(date -d '10 minutes ago' +%s000) \
  --query 'events[].message' --output json \
| python3 -c "import json,sys; [print(x) for x in json.load(sys.stdin)]" \
| grep -Ei "Per-control LLM assessment|Exception|ERROR|Throttling|AccessDenied|ResourceNotFound"
```

Common causes:

- `ResourceNotFoundException`: model ID unavailable in the selected region, or an incorrect model ID was configured.
- `AccessDeniedException`: ECS task role policy does not allow `bedrock:InvokeModel` on the selected model ARN.
- `ThrottlingException` with `Too many tokens per day`: account-applied Bedrock runtime quota is effectively `0`.

### 2) Verify Bedrock model availability and quota state

Check model availability state:

```bash
aws bedrock get-foundation-model-availability \
  --model-id amazon.nova-pro-v1:0 \
  --region ap-southeast-2
```

Compare account-applied quotas vs AWS defaults:

```bash
# Account-applied quotas
aws service-quotas list-service-quotas \
  --service-code bedrock \
  --region ap-southeast-2 \
  --query 'Quotas[?contains(QuotaName, `Nova Pro`) && (contains(QuotaName, `tokens`) || contains(QuotaName, `requests`))].[QuotaName,Value,Adjustable]' \
  --output table

# AWS default quotas
aws service-quotas list-aws-default-service-quotas \
  --service-code bedrock \
  --region ap-southeast-2 \
  --query 'Quotas[?contains(QuotaName, `Nova Pro`) && (contains(QuotaName, `tokens`) || contains(QuotaName, `requests`))].[QuotaName,Value,Adjustable]' \
  --output table
```

If account-applied runtime quotas are `0` while defaults are non-zero, open an AWS Support case to enable/activate Bedrock runtime quota for the account and region.

### 3) Confirm ECS is running the expected task definition and image

```bash
aws ecs describe-services \
  --cluster ecs-rag-dev-apse2 \
  --services svc-query-web-rag-dev-apse2 \
  --region ap-southeast-2 \
  --query 'services[0].{taskDefinition:taskDefinition,runningCount:runningCount,desiredCount:desiredCount}'

aws ecs describe-task-definition \
  --task-definition td-query-web-rag-dev-apse2:31 \
  --region ap-southeast-2 \
  --query 'taskDefinition.containerDefinitions[0].image'
```

If IAM policies changed but behaviour did not, force a service redeploy so new tasks assume updated permissions:

```bash
aws ecs update-service \
  --cluster ecs-rag-dev-apse2 \
  --service svc-query-web-rag-dev-apse2 \
  --force-new-deployment \
  --region ap-southeast-2
```

### 4) Verify the task role policy for Bedrock model ARN

```bash
aws iam get-role-policy \
  --role-name role-ecs-task-rag-dev-apse2 \
  --policy-name bedrock-invoke \
  --query 'PolicyDocument' --output json
```

Ensure the selected model ARN is included, for example:

- `arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-pro-v1:0`

### 5) Corpus-B ingestion auth and file-type pitfalls

For `/api/corpus-b/ingest`, auth is expected as multipart form field `auth_token` (not `Authorization` header):

```bash
curl -s -X POST "$ALB/api/corpus-b/ingest?reindex_on_dedupe=true&trigger_job=true" \
  -F "auth_token=$TOKEN" \
  -F "files=@/path/to/file.pdf"
```

Allowed file types are office/pdf/html families (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.html`, etc). Markdown (`.md`) is rejected by design.

## Known Issue (Paused): CAE -> AMW Managed Prometheus Ingestion

Status: **Not working as of 2026-05-02. Implementation is paused for now.**

What is verified:

- `query_web` exposes `/metrics` and returns HTTP 200 with valid Prometheus text payload.
- Container App Environment (CAE) has both DCR and DCE associations configured.
- CAE associations were updated to AMW default ingestion settings (`MA_amw-*` managed resource group).
- EasyAuth excludes `/metrics`.
- Azure Monitor Workspace (AMW) Prometheus queries still return empty vectors for `up` and application metric names.

Known-good verification commands:

```bash
# Verify /metrics is reachable
curl -s -o /tmp/metrics.out -w "%{http_code}\n" "https://<query-fqdn>/metrics" && head -n 8 /tmp/metrics.out

# Verify CAE DCR/DCE associations
az monitor data-collection rule association list \
  --resource "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.App/managedEnvironments/<cae-name>" \
  -o table

# Query AMW Prometheus endpoint
endpoint=$(az monitor account show -g <rg> -n <amw-name> --query metrics.prometheusQueryEndpoint -o tsv)
token=$(az account get-access-token --resource https://prometheus.monitor.azure.com --query accessToken -o tsv)
curl -s -G "$endpoint/api/v1/query" --data-urlencode 'query=up' -H "Authorization: Bearer $token"
curl -s -G "$endpoint/api/v1/query" --data-urlencode 'query=python_gc_objects_collected_total' -H "Authorization: Bearer $token"
```

If resuming this work later:

1. Re-run the three checks above.
2. Confirm CAE associations still point to AMW default DCR/DCE resources.
3. If vectors remain empty after propagation windows, raise a Microsoft support ticket with collected evidence.

### Resolve deployment variables

See `infra/terraform/outputs.tf` for list of Azure resources that can be obtained from Terraform and the string to use to obtain the resource name

```bash
TF_DIR="infra/terraform/azure"
RG="rg-ai-platform-${TARGET_ENV}"

terraform -chdir="${TF_DIR}" init \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"

QUERY_FQDN=$(terraform -chdir="${TF_DIR}" output -raw query_web_fqdn)
COSMOS_ACCOUNT=$(az cosmosdb list -g "${RG}" --query "[0].name" -o tsv)
SEARCH_ENDPOINT=$(terraform -chdir="${TF_DIR}" output -raw search_endpoint 2>/dev/null || true)
```

If docker image has not deployed after running `rollout-agent-hosting.sh` even though the image has been created and deployed to ACR using `build-push-<container>.sh`, make sure that `infra/terraform/azure/environments/<env>/<env>.tfvars` has been updated with the corresponding immutable `<container>-image-tag` value.

If unable to auth to the web app or diagnostic pages after deployment, make sure that the web redirect url has been applied. `rollout-agent-hosting.sh` provides the command to apply if it has not been able to detect the presence of the callback on completion of the deployment.

### Health check

```bash
curl "https://${QUERY_FQDN}/health"
# Expected: {"status":"ok","service":"rag-query-web",...}
```

### Configuration check

```bash
curl "https://${QUERY_FQDN}/api/config"
# Verify: default_temperature, evaluator_temperature, prompt_injection_validator_temperature
```

---

## Docker (Jumpbox)

### No space left on device during build

```bash
# 1. Check what is full
sudo docker info --format '{{.DockerRootDir}}'
sudo df -h /var/lib/docker
sudo df -ih /var/lib/docker
sudo docker system df -v

# 2. Safe cleanup (stopped resources only)
sudo docker builder prune -af
sudo docker image prune -af
sudo docker container prune -f
sudo docker volume prune -f

# 3. Full reclaim if still tight
sudo docker system prune -af --volumes
```

---

## Azure Container Registry

```bash
ACR="acrdevaue04"   # replace with your ACR name

# List repositories
az acr repository list --name "${ACR}" --output table

# List image tags, newest first
az acr repository show-tags --name "${ACR}" --repository rag-query-web \
  --orderby time_desc --output table

az acr repository show-tags --name "${ACR}" --repository rag-ingestion \
  --orderby time_desc --output table

az acr repository show-tags --name "${ACR}" --repository rag-confluence-poller \
  --orderby time_desc --output table
```

---
## Subscription ID
SUB=$(az account show --query id --output tsv)

## Container Apps

```bash
RG="rg-ai-platform-${TARGET_ENV}"

# --- Query web app ---
APP="ca-query-web-${TARGET_ENV}-aue-XXXXXXXX"   # replace suffix

# Show current environment variables
az containerapp show -g "${RG}" -n "${APP}" \
  --query "properties.template.containers[0].env" -o table

# Filter to a specific variable pattern
az containerapp show -g "${RG}" -n "${APP}" \
  --query "properties.template.containers[0].env[?contains(name,'AZURE_COSMOS')]"

# Set an environment variable
az containerapp update -g "${RG}" -n "${APP}" \
  --set-env-vars AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME=orchestration-state

# Restart the app (to pick up env var changes)
az containerapp revision restart -g "${RG}" -n "${APP}" --revision "$(
  az containerapp show -g "${RG}" -n "${APP}" \
    --query "properties.latestRevisionName" -o tsv
)"

# Stream live logs
az containerapp logs show -g "${RG}" -n "${APP}" --follow --tail 100
```

### Container App Jobs (ingestion, Confluence poller)

```bash
JOB="caj-ingestion-${TARGET_ENV}-aue-XXXXXXXX"   # replace suffix

# List recent executions
az containerapp job execution list \
  --resource-group "${RG}" \
  --name "${JOB}" \
  -o table

# Get container name from the job template
CONTAINER_NAME=$(az containerapp job show -g "${RG}" -n "${JOB}" \
  --query "properties.template.containers[0].name" -o tsv)
# Typical values: ingestion-runner, confluence-poller

# Stream logs for the latest execution
az containerapp job logs show \
  -g "${RG}" -n "${JOB}" \
  --container "${CONTAINER_NAME}" \
  --follow --tail 200

# Stream logs for a specific execution
EXECUTION_NAME="caj-ingestion-${TARGET_ENV}-aue-XXXXXXXX-mrpi52v"

az containerapp job logs show \
  -g "${RG}" -n "${JOB}" \
  --execution "${EXECUTION_NAME}" \
  --container "${CONTAINER_NAME}" \
  --tail 200

# Kill a running job execution
az containerapp job stop \
  -g "${RG}" -n "${JOB}" \
  --job-execution-name "${EXECUTION_NAME}"
```

### Log Analytics (KQL)

N.B. will need to replace resource names with names used in implementation.

Verify that log data is being written to Log Analytics Workspace

```kql
law_id=$(az monitor log-analytics workspace show -g rg-ai-platform-dev -n law-dev-aue-04 --query customerId -o tsv)
az monitor log-analytics query -w "$law_id" --analytics-query "ContainerAppSystemLogs_CL | where TimeGenerated > ago(2h) | where ContainerAppName_s == 'ca-rag-query-dev-aue-20260408' | project TimeGenerated, Reason_s, Log_s | order by TimeGenerated desc | take 20" -o table
```

If you receive `SEM0100` for `ContainerAppSystemLogs_CL`, verify you are querying the Log Analytics workspace (`az monitor log-analytics query`) and not the AMW Prometheus endpoint.

Broad search across all Container App logs for a job:

```kql
union isfuzzy=true
(
  ContainerAppConsoleLogs_CL
  | extend LogText = tostring(coalesce(
      column_ifexists("Log_s",""),
      column_ifexists("LogMessage_s",""),
      column_ifexists("Message","")))
),
(
  ContainerAppSystemLogs_CL
  | extend LogText = tostring(coalesce(
      column_ifexists("Log_s",""),
      column_ifexists("Message",""),
      column_ifexists("Reason_s","")))
)
| where TimeGenerated > ago(24h)
| where LogText contains "caj-ingestion-dev-aue-20260408"
| where LogText has_any (dynamic(["controls_source_prefix","source_files_downloaded","controls","ingestion"]))
| project TimeGenerated, LogText
| order by TimeGenerated desc
```

Logs for a specific execution:

```kql
let exec = "caj-ingestion-dev-aue-20260408-mrpi52v";
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| where ContainerGroupName_s startswith exec
| extend Msg = coalesce(
    column_ifexists("Log_s",""),
    column_ifexists("LogMessage_s",""),
    column_ifexists("Message",""),
    tostring(RawData))
| project TimeGenerated, ContainerGroupName_s, ContainerName_s, Msg
| order by TimeGenerated asc
```

Query web app errors in the last hour:

```kql
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1h)
| extend Msg = coalesce(column_ifexists("Log_s",""), column_ifexists("Message",""), tostring(RawData))
| where Msg has_any (dynamic(["ERROR","Exception","Traceback","500","failed"]))
| project TimeGenerated, ContainerName_s, Msg
| order by TimeGenerated desc
```

---

## Key Vault

```bash
KV="kv-${TARGET_ENV}-aue-XXXXXXXX"   # replace suffix

# List secrets
az keyvault secret list --vault-name "${KV}" -o table

# Check a specific secret exists
az keyvault secret show --vault-name "${KV}" --name "jumpbox-admin-ssh-public-key-${TARGET_ENV}" \
  --query "attributes.enabled" -o tsv

# Check who has access
az keyvault show --name "${KV}" --query "properties.accessPolicies" -o table
```

**Note:** Terraform Key Vault-backed inputs fail plan early if the secret is missing. Ensure the bootstrap publish step has written the secret before running a root plan or apply.

---

## Azure Storage

```bash
ACCOUNT=$(terraform -chdir="${TF_DIR}" output -raw storage_account_name)
CONTAINER="grounding-data"

# Show metadata for a specific blob
az storage blob show \
  --account-name "${ACCOUNT}" --container-name "${CONTAINER}" \
  --name "corpus-b/by-dedupe/<blob-id>" \
  --auth-mode login \
  --query "{name:name,metadata:metadata,lastModified:properties.lastModified}" -o json

# List all blobs in the container
az storage blob list \
  --account-name "${ACCOUNT}" --container-name "${CONTAINER}" \
  --auth-mode login --query "[].name" -o tsv

# List by corpus prefix
az storage blob list --account-name "${ACCOUNT}" --container-name "${CONTAINER}" \
  --prefix "corpus-a/source/" --auth-mode login --query "[].name" -o tsv

az storage blob list --account-name "${ACCOUNT}" --container-name "${CONTAINER}" \
  --prefix "corpus-b/by-dedupe/" --auth-mode login --query "[].name" -o tsv

az storage blob list --account-name "${ACCOUNT}" --container-name "${CONTAINER}" \
  --prefix "corpus-c/by-dedupe/" --auth-mode login --query "[].name" -o tsv

# Count blobs in a prefix
az storage blob list --account-name "${ACCOUNT}" --container-name "${CONTAINER}" \
  --prefix "corpus-a/source/" --auth-mode login --query "length([])" -o tsv

# Delete all blobs for a corpus (destructive — confirm before running)
az storage blob delete-batch --account-name "${ACCOUNT}" --source "${CONTAINER}" \
  --pattern "corpus-a/by-dedupe/*" --auth-mode login

az storage blob delete-batch --account-name "${ACCOUNT}" --source "${CONTAINER}" \
  --pattern "corpus-b/by-dedupe/*" --auth-mode login

az storage blob delete-batch --account-name "${ACCOUNT}" --source "${CONTAINER}" \
  --pattern "corpus-c/by-dedupe/*" --auth-mode login
```

---

## Cosmos DB

```bash
RG="rg-ai-platform-${TARGET_ENV}"
COSMOS_ACCOUNT=$(az cosmosdb list -g "${RG}" --query "[0].name" -o tsv)

# Verify database and containers exist
az cosmosdb sql database show \
  -a "${COSMOS_ACCOUNT}" -g "${RG}" -n "rag-conversations"

az cosmosdb sql container show \
  -a "${COSMOS_ACCOUNT}" -g "${RG}" \
  -d "rag-conversations" -n "conversations"

az cosmosdb sql container show \
  -a "${COSMOS_ACCOUNT}" -g "${RG}" \
  -d "rag-conversations" -n "orchestration-state"

# Check managed identity role assignment
az role assignment list \
  --scope "/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.DocumentDB/databaseAccounts/${COSMOS_ACCOUNT}" \
  --query "[?roleDefinitionName=='Cosmos DB Built-in Data Contributor'].{principal:principalName,role:roleDefinitionName}" \
  -o table

# View Cosmos DB metrics (request units, throttling)
az cosmosdb show -g "${RG}" -n "${COSMOS_ACCOUNT}" \
  --query "documentEndpoint" -o tsv
# Then check Azure Portal → Cosmos DB → Metrics for 429 throttle errors
```

### Common Cosmos DB Issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `CosmosDB unavailable: ...` in app logs | Private endpoint not reachable or managed identity missing role | Verify private endpoint DNS, check `Cosmos DB Built-in Data Contributor` role assignment |
| Conversations not persisting after restart | App fell back to in-memory mode | Check container app env vars — `AZURE_COSMOS_ENDPOINT`, `AZURE_COSMOS_DATABASE_NAME`, `AZURE_COSMOS_CONTAINER_NAME` must all be set |
| `404` on conversation lookup | Wrong `user_id` partition key or container `orchestration-state` vs `conversations` mismatch | Verify `AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME` is set to `orchestration-state` (separate from conversation container) |
| Cosmos DB throttling (429) | Insufficient RU/s | Increase container throughput: `az cosmosdb sql container throughput update -a <acct> -g <rg> -d rag-conversations -n conversations --throughput 1000` |

---

## Azure Foundry / Azure OpenAI

```bash
RG="rg-ai-platform-${TARGET_ENV}"
FOUNDRY_NAME=$(terraform -chdir="${TF_DIR}" output -raw ai_services_endpoint | sed 's|https://||; s|\.cognitiveservices.*||')

# List Foundry accounts
az cognitiveservices account list -g "${RG}" -o table

# Get Foundry endpoint
az cognitiveservices account show -g "${RG}" -n "${FOUNDRY_NAME}" \
  --query "properties.endpoint" -o tsv

# List model deployments
az cognitiveservices account deployment list \
  -g "${RG}" -n "${FOUNDRY_NAME}" \
  --query "[].{name:name,model:properties.model.name,capacity:sku.capacity}" -o table

# Verify managed identity has Cognitive Services User role
az role assignment list \
  --scope "/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.CognitiveServices/accounts/${FOUNDRY_NAME}" \
  --query "[?roleDefinitionName=='Cognitive Services User'].{principal:principalName}" \
  -o table
```

### Common Foundry Issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `401 Unauthorized` on Foundry API | Managed identity missing `Cognitive Services User` role | Add role assignment on the Foundry account |
| `404` on model deployment | Deployment name mismatch | Check container app env vars `QUERY_DEPLOYMENT`, `EMBEDDING_DEPLOYMENT`; confirm against `az cognitiveservices account deployment list` |
| Temperature errors in logs (`Model rejected temperature`) | Some Foundry model versions reject non-1.0 temperatures | App auto-retries at 1.0 — this is expected. Adjust `DEFAULT_TEMPERATURE` if needed. |
| Slow responses / timeouts | Token quota or model capacity | Check Foundry metrics in portal; consider increasing deployment capacity |

---

## Azure AI Search

```bash
SEARCH_ENDPOINT=$(terraform -chdir="${TF_DIR}" output -raw search_endpoint)

# List indexes
az search index list --service-name "${SEARCH_ENDPOINT}" \
  --resource-group "${RG}" -o table

# Check indexer status
az search indexer status --service-name "${SEARCH_ENDPOINT}" \
  --resource-group "${RG}" --name "grounding-index-indexer"
```

### Full Reset — remove blobs and index

Run from within the project with venv activated, from a host with network access to the private endpoints:

```bash
SEARCH_ENDPOINT=$(terraform -chdir="${TF_DIR}" output -raw search_endpoint) \
AI_SERVICES_ENDPOINT=$(terraform -chdir="${TF_DIR}" output -raw ai_services_endpoint) \
AZURE_OPENAI_ENDPOINT=$(terraform -chdir="${TF_DIR}" output -raw openai_endpoint) \
AZURE_STORAGE_ACCOUNT_NAME=$(terraform -chdir="${TF_DIR}" output -raw storage_account_name) \
AZURE_STORAGE_RESOURCE_ID="/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.Storage/storageAccounts/$(terraform -chdir="${TF_DIR}" output -raw storage_account_name)" \
python -m runtime.ingestion.runner --mode reset
```

---

## Diagnostic Endpoints

All diagnostic endpoints require `?auth_token=<QUERY_WEB_AUTH_TOKEN>` if auth is enabled and respond with JSON. They are **disabled when `TARGET_ENV=prod`**.

```bash
QUERY_FQDN=$(terraform -chdir="${TF_DIR}" output -raw query_web_fqdn)
TOKEN="<QUERY_WEB_AUTH_TOKEN>"   # omit if auth not configured
```

| Endpoint | What it shows |
|----------|--------------|
| `/api/diagnostics/ingestion/overview` | Ingestion job status, last run, blob/index counts |
| `/api/diagnostics/search/resources` | All indexes, indexers, skillsets, data sources and their status |
| `/api/diagnostics/search/indexer-history` | Indexer execution history with item counts and errors |
| `/api/diagnostics/search/datasource-connectivity` | Data source config and blob enumeration test |
| `/api/diagnostics/search/field-mappings` | Indexer → index field mapping validation |
| `/api/diagnostics/search/index-samples` | Sample documents from the grounding index |
| `/api/diagnostics/storage/blobs` | Blob inventory listing for the grounding-data container |
| `/api/diagnostics/storage/metadata-validation` | Validates required ingestion metadata on blobs |
| `/api/diagnostics/acr/images` | Lists images and tags from the connected ACR |

```bash
# Example calls
curl "https://${QUERY_FQDN}/api/diagnostics/ingestion/overview?auth_token=${TOKEN}"
curl "https://${QUERY_FQDN}/api/diagnostics/search/resources?auth_token=${TOKEN}"
curl "https://${QUERY_FQDN}/api/diagnostics/search/indexer-history?auth_token=${TOKEN}"
curl "https://${QUERY_FQDN}/api/diagnostics/storage/metadata-validation?prefix=corpus-b/by-dedupe/&auth_token=${TOKEN}"
curl "https://${QUERY_FQDN}/api/diagnostics/storage/metadata-validation?prefix=corpus-b/by-dedupe/&sample_size=10&include_values=true&auth_token=${TOKEN}"
curl "https://${QUERY_FQDN}/api/diagnostics/storage/metadata-validation?prefix=corpus-c/by-dedupe/&auth_token=${TOKEN}"
```

---

## Common Error Patterns

| Error message | Where seen | Cause | Fix |
|--------------|-----------|-------|-----|
| `Internal server error; check logs for details.` | API responses | Unhandled exception — details are in container app logs, not the response | Check Log Analytics / `az containerapp logs show` |
| `CosmosDB unavailable: ...` | App startup log | Cosmos DB unreachable; app fell back to in-memory | Verify private endpoint, managed identity role, and env vars |
| `RuntimeError: AZURE_COSMOS_ENDPOINT not set` | App startup | Missing env var | Add `AZURE_COSMOS_ENDPOINT` to Container App environment |
| `openai package is required for Foundry API integration` | LLM calls | `openai` not installed in container image | Verify `query_web/requirements/base.txt` and provider profile requirements, then rebuild image |
| `Diagnostics endpoints are disabled when TARGET_ENV is 'prod'` | Diagnostic endpoints | `TARGET_ENV=prod` blocks diagnostic access | Use a non-prod environment or check logs directly |
| `no space left on device` | Docker build on jumpbox | Docker data directory full | See Docker section above |
| `401 Unauthorized` on `/ask` or `/api/ask` | Browser / API client | `QUERY_WEB_AUTH_TOKEN` mismatch or missing | Pass correct `auth_token` param; check container app env var |
| Indexer showing `transientFailure` repeatedly | Azure AI Search | Blob metadata missing required fields | Run metadata-validation diagnostic; re-ingest with correct metadata |
| `422 Unprocessable Entity` on conversation endpoints | API client | Missing required form fields (`user_id`, `role`, `content`) | See [foundry-conversations.md](foundry-conversations.md) for correct field names |

---

## Integration Tests

```bash
# Run against a deployed environment (from jumpbox with network access)
QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/azure/run-query-web-integration-tests.sh "https://${QUERY_FQDN}" "<optional-auth-token>"

# Run unit tests locally
source runtime/.venv/bin/activate
python3 -m pytest tests/unit/ -q
```
