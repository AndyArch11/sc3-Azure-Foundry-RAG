# Foundry Chat Completion & Conversation Setup Guide

## Overview

This guide covers deploying Azure Foundry chat completion functionality and persistent conversation history via Cosmos DB for the query web application.

## Prerequisites

- Terraform-managed environment with:
  - Chat completion deployment (e.g., `gpt-5.1-chat`)
  - Embedding deployment (e.g., `text-embedding-ada-002`)
  - Evaluation/reasoning deployment (e.g., `gpt-4.1-mini`)
- Cosmos DB SQL API account, database, and container created by Terraform
- Managed identity or operator identity with roles:
  - `Cognitive Services User` on Foundry account
  - `Cosmos DB Built-in Data Contributor` on Cosmos DB account
- Python 3.12+

## Step 1: Update Environment Variables

### Query Web Container App Environment

The query web Container App receives these values from Terraform-managed environment variables:

```bash
# Foundry API
AZURE_OPENAI_ENDPOINT=https://foundry-<suffix>.openai.azure.com/
AZURE_SEARCH_ENDPOINT=https://srch-<suffix>.search.windows.net
AZURE_SEARCH_INDEX_NAME=grounding-index

# Cosmos DB (conversation store)
AZURE_COSMOS_ENDPOINT=https://cosmos-<suffix>.documents.azure.com:443/
AZURE_COSMOS_DATABASE_NAME=rag-conversations
AZURE_COSMOS_CONTAINER_NAME=conversations

# Optional
DEFAULT_TEMPERATURE=1.0
EVALUATOR_TEMPERATURE=1.0
PROMPT_INJECTION_VALIDATOR_TEMPERATURE=0.5
ACCEPTABLE_SCORE_THRESHOLD=0.72
QUERY_WEB_AUTH_TOKEN=<optional-auth-token>
```

### Resolve Values

Replace `<suffix>` with your deployment suffix (e.g., "dev-eastus-abc123").

Retrieve values from:
```bash
TARGET_ENV="<env>"
TF_DIR="infra/terraform/azure"

terraform -chdir="${TF_DIR}" init \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"

RG_NAME=$(terraform -chdir="${TF_DIR}" output -raw resource_group_name)
QUERY_FQDN=$(terraform -chdir="${TF_DIR}" output -raw query_web_fqdn)

COSMOS_ENDPOINT=$(az cosmosdb list \
  -g "${RG_NAME}" \
  --query "[0].documentEndpoint" \
  -o tsv)

FOUNDRY_ENDPOINT=$(az cognitiveservices account list \
  -g "${RG_NAME}" \
  --query "[?kind=='AIServices'][0].properties.endpoint" \
  -o tsv)
```

---

## Step 2: Provision Cosmos DB Data Plane

The current Terraform stack creates the Cosmos DB SQL database and container automatically. Manual creation should only be used for one-off recovery or investigation.

Verify the data plane exists:

```bash
TARGET_ENV="<env>"
TF_DIR="infra/terraform/azure"

terraform -chdir="${TF_DIR}" init \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"

RG_NAME=$(terraform -chdir="${TF_DIR}" output -raw resource_group_name)
COSMOS_ACCOUNT=$(az cosmosdb list -g "${RG_NAME}" --query "[0].name" -o tsv)

az cosmosdb sql database show \
  -a "${COSMOS_ACCOUNT}" \
  -g "${RG_NAME}" \
  -n "rag-conversations"

az cosmosdb sql container show \
  -a "${COSMOS_ACCOUNT}" \
  -g "${RG_NAME}" \
  -d "rag-conversations" \
  -n "conversations"
```

The application uses managed identity for runtime access. Do not add Cosmos account keys to the application configuration.

---

## Step 3: Build And Roll Out Query Web

Build and push from a Docker-capable host inside the VNet:

```bash
TARGET_ENV="<env>"
QUERY_TAG="$(date +%Y%m%d%H%M)-<gitsha>"

ENV="${TARGET_ENV}" IMAGE_TAG="${QUERY_TAG}" ./ops/scripts/azure/build-push-query-web.sh

terraform -chdir=infra/terraform/azure apply \
  -input=false \
  -var-file="environments/${TARGET_ENV}/bootstrap.generated.tfvars" \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars" \
  -var "query_web_image_tag=${QUERY_TAG}" \
  -target=module.agent_hosting
```

Query web dependencies of interest are in [query_web/requirements.txt](../query_web/requirements.txt).

---

## Step 4: Verify Deployment

Resolve the application FQDN from Terraform:

```bash
TARGET_ENV="<env>"
QUERY_FQDN=$(terraform -chdir=infra/terraform/azure output -raw query_web_fqdn)
```

### Health Check

```bash
curl "https://${QUERY_FQDN}/health"
# Expected: {"status":"ok","service":"rag-query-web",...}
```

### Configuration Endpoint

```bash
curl "https://${QUERY_FQDN}/api/config"
# Expected: {..., "default_temperature": 1.0, ...}
# Also exposed: evaluator_temperature and prompt_injection_validator_temperature
```

### Integration Smoke Test

```bash
QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=true \
./ops/scripts/azure/run-query-web-integration-tests.sh "https://${QUERY_FQDN}" "<optional-auth-token>"
```

### Create A Conversation

```bash
curl -X POST "https://${QUERY_FQDN}/api/conversations/new" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "auth_token=<optional-auth-token>"
```

### View Conversation History

```bash
USER_ID="<user-id>"
CONV_ID="<conversation-id>"

curl "https://${QUERY_FQDN}/api/conversations/${USER_ID}/${CONV_ID}?auth_token=<optional-auth-token>"
```

---

## Step 5: Test With Python

```python
import requests
from urllib.parse import urlencode

BASE_URL = "https://<query-web-fqdn>"
AUTH_TOKEN = ""

# 1. Create conversation
resp = requests.post(
    f"{BASE_URL}/api/conversations/new",
    data={"auth_token": AUTH_TOKEN}
)
session = resp.json()
conversation_id = session["conversation_id"]
user_id = session["user_id"]
print(f"Created conversation: {conversation_id}")

# 2. Add a message to the conversation
message_data = {
    "user_id": user_id,
    "role": "user",
    "content": "What are the AESCSF controls?",
    "auth_token": AUTH_TOKEN,
}

resp = requests.post(
    f"{BASE_URL}/api/conversations/{conversation_id}/message",
    data=urlencode(message_data),
    headers={"Content-Type": "application/x-www-form-urlencoded"}
)
print(f"Response: {resp.json()}")

# 3. Fetch conversation history
resp = requests.get(
    f"{BASE_URL}/api/conversations/{user_id}/{conversation_id}",
    params={"auth_token": AUTH_TOKEN},
)
history = resp.json()
for msg in history["messages"]:
    print(f"{msg['role'].upper()}: {msg['content'][:100]}...")
```

---

## Monitoring & Troubleshooting

### Check Cosmos DB Activity

```bash
az cosmosdb show -g <resource-group> -n <cosmos-account-name> \
  --query "documentEndpoint" -o tsv
# Then view metrics in Azure Portal -> Cosmos DB account -> Metrics
```

### View Container App Logs

```bash
az containerapp logs show \
  -g <resource-group> \
  -n <query-web-app-name> \
  --tail 50

# Or via Log Analytics (if configured)
az monitor log-analytics query \
  -w <workspace-id> \
  --analytics-query "ContainerAppConsoleLogs | tail 50"
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `RuntimeError: AZURE_COSMOS_ENDPOINT not set` | Missing env var | Check Container App environment configuration |
| `CosmosDB unavailable: ...` | Connection failed | Verify private endpoint, network ACLs, managed identity role |
| `401 Unauthorized` on Foundry API | Token expired or invalid | Ensure managed identity has `Cognitive Services User` role |
| `conversation_id` parameter ignored | Session not found | Create new conversation via `/api/conversations/new` first |
| Slow conversation retrieval | CosmosDB throttling | Increase container throughput via `az cosmosdb sql container throughput update` |

---

## Performance Tuning

### Cosmos DB Throughput

For production workloads:

```bash
# Recommended: 800-1000 RU/s for active conversation store
az cosmosdb sql container throughput update \
  -a "<cosmos-account-name>" \
  -g "<resource-group>" \
  -d "rag-conversations" \
  -n "conversations" \
  --throughput 1000
```

### TTL (Time-to-Live) for Archival

Auto-purge old conversations:

```python
# Set TTL on container (86400 seconds = 1 day, -1 = disable)
container_client.replace_container(
    id="conversations",
    partition_key="/user_id",
    default_ttl=2592000,  # 30 days
)
```

---

## Security Hardening

### 1. RBAC for Cosmos DB

```bash
# Grant Cosmos DB Built-in Data Contributor
az role assignment create \
  --role "Cosmos DB Built-in Data Contributor" \
  --assignee-object-id <service-principal-id> \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.DocumentDB/databaseAccounts/<cosmos-name>
```

### 2. Network Isolation

Ensure private endpoints:
- Foundry account → private endpoint in private_endpoint_subnet
- CosmosDB account → private endpoint in private_endpoint_subnet

### 3. Data Encryption

- At rest: customer-managed keys can be added later if required by policy
- In transit: TLS 1.2+ enforced

---

## Rollback Plan

If issues arise:

1. **Disable conversation history** (keep Foundry API):
   - Stop using the conversation endpoints and fall back to single-turn `/api/ask` requests.
   - App continues to function for single-turn queries

2. **Roll back query web image**:
   ```bash
   TARGET_ENV="<env>"
   ROLLBACK_TAG="<previous-query-web-tag>"

   terraform -chdir=infra/terraform/azure apply \
     -input=false \
     -var-file="environments/${TARGET_ENV}/bootstrap.generated.tfvars" \
     -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars" \
     -var "query_web_image_tag=${ROLLBACK_TAG}" \
     -target=module.agent_hosting
   ```

3. **Preserve Cosmos DB data**:
   - Conversation documents auto-persisted with partition key `/user_id`
   - Can be recovered if service restarted

---

## Next Steps

- Monitor conversation patterns and usage via Cosmos DB metrics
- Implement conversation search (full-text search on message content)
- Add conversation export (download as Markdown)
- Set up alerts for CosmosDB throttling and error rates
