# Foundry Chat Completion & Conversation Setup Guide

## Overview

This guide covers deploying the new Azure Foundry API chat completion functionality and persistent conversation history via CosmosDB.

## Prerequisites

- Azure Foundry account with:
  - Chat completion deployment (e.g., `gpt-5.1-chat`)
  - Embedding deployment (e.g., `text-embedding-ada-002`)
  - Evaluation/reasoning deployment (e.g., `gpt-4.1-mini`)
- CosmosDB Serverless account (NoSQL API)
- Service Principal or Managed Identity with roles:
  - `Cognitive Services User` on Foundry account
  - `Cosmos DB Built-in Data Contributor` on CosmosDB account
- Python 3.12+

## Step 1: Update Environment Variables

### Container App Environment

Add the following to the Container App's environment variables:

```bash
# Foundry API
AZURE_OPENAI_ENDPOINT=https://foundry-<suffix>.openai.azure.com/
AZURE_SEARCH_ENDPOINT=https://srch-<suffix>.search.windows.net
AZURE_SEARCH_INDEX_NAME=grounding-index

# CosmosDB (Conversation Store)
AZURE_COSMOS_ENDPOINT=https://cosmos-<suffix>.documents.azure.com:443/
AZURE_COSMOS_DATABASE_NAME=rag-conversations
AZURE_COSMOS_CONTAINER_NAME=conversations

# Optional
DEFAULT_TEMPERATURE=1.0
ACCEPTABLE_SCORE_THRESHOLD=0.72
QUERY_WEB_AUTH_TOKEN=<optional-auth-token>
```

### Substitution

Replace `<suffix>` with your deployment suffix (e.g., "dev-eastus-abc123").

Retrieve values from:
```bash
# Get resource names
az resource list -g rg-<suffix> --query "[].{Name:name, Type:type}" -o table

# Get CosmosDB endpoint
az cosmosdb show -g rg-<suffix> -n cosmos-<suffix> --query documentEndpoint -o tsv

# Get CosmosDB key (if not using managed identity)
az cosmosdb list-keys -g rg-<suffix> -n cosmos-<suffix> --query primaryMasterKey -o tsv
```

---

## Step 2: Provision CosmosDB Database & Container

If not auto-provisioned via Terraform, create manually:

### Via Azure CLI

```bash
COSMOS_ACCOUNT="cosmos-dev-eastus"
RESOURCE_GROUP="rg-foundry-dev"

# Create database
az cosmosdb sql database create \
  -a "$COSMOS_ACCOUNT" \
  -g "$RESOURCE_GROUP" \
  -n "rag-conversations"

# Create container (partitioned by user_id)
az cosmosdb sql container create \
  -a "$COSMOS_ACCOUNT" \
  -g "$RESOURCE_GROUP" \
  -d "rag-conversations" \
  -n "conversations" \
  --partition-key-path "/user_id" \
  --throughput 400
```

### Via Python SDK

```python
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

endpoint = "https://cosmos-dev-eastus.documents.azure.com:443/"
credential = DefaultAzureCredential()

client = CosmosClient(url=endpoint, credential=credential)
db_client = client.create_database_if_not_exists(id="rag-conversations")
container_client = db_client.create_container_if_not_exists(
    id="conversations",
    partition_key="/user_id",
    offer_throughput=400,
)
print(f"Container created: {container_client.id}")
```

---

## Step 3: Update query_web Dependencies

### requirements.txt

Ensure the following are included:

```txt
fastapi==0.115.6
uvicorn==0.34.0
jinja2==3.1.6
python-multipart==0.0.22
requests==2.32.4
azure-identity==1.21.0
azure-search-documents==11.6.0
openai==1.51.0               # NEW: Foundry API via OpenAI SDK
azure-cosmos==4.7.0          # NEW: Conversation persistence
```

### Build & Deploy

```bash
cd query_web
pip install -r requirements.txt
docker build -t query-web:latest .
docker push <acr>.azurecr.io/query-web:latest

# Update Container App with new image
az containerapp update \
  --resource-group <rg> \
  --name query-web-ca \
  --image <acr>.azurecr.io/query-web:latest
```

---

## Step 4: Verify Deployment

### Health Check

```bash
curl https://<query-web-fqdn>/health
# Expected: {"status":"ok","service":"rag-query-web",...}
```

### Configuration Endpoint

```bash
curl https://<query-web-fqdn>/api/config
# Expected: {..., "default_temperature": 1.0, ...}
```

### Create a Conversation

```bash
curl -X POST https://<query-web-fqdn>/api/conversations/new \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "auth_token=optional"
# Expected:
# {
#   "session_id": "550e8400-e29b-41d4-a716-446655440000",
#   "conversation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
#   "user_id": "5a4b8d2e"
# }
```

### Query with Conversation Context

```bash
curl -X POST https://<query-web-fqdn>/ask \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "question=What%20is%20cybersecurity?" \
  -d "retrieve_k=5" \
  -d "temperature=1.0" \
  -d "session_id=550e8400-e29b-41d4-a716-446655440000" \
  -d "conversation_id=f47ac10b-58cc-4372-a567-0e02b2c3d479"
```

### View Conversation History

```bash
USER_ID="5a4b8d2e"
CONV_ID="f47ac10b-58cc-4372-a567-0e02b2c3d479"

curl "https://<query-web-fqdn>/api/conversations/$USER_ID/$CONV_ID?auth_token=optional"
```

---

## Step 5: Test with Python

```python
import requests
from urllib.parse import urlencode

BASE_URL = "https://<query-web-fqdn>"

# 1. Create conversation
resp = requests.post(
    f"{BASE_URL}/api/conversations/new",
    data={"auth_token": ""}
)
session = resp.json()
conversation_id = session["conversation_id"]
user_id = session["user_id"]
print(f"Created conversation: {conversation_id}")

# 2. Add a question
query_data = {
    "question": "What are the AESCSF controls?",
    "retrieve_k": 5,
    "temperature": 1.0,
    "session_id": session["session_id"],
    "conversation_id": conversation_id,
}

resp = requests.post(
    f"{BASE_URL}/ask",
    data=urlencode(query_data),
    headers={"Content-Type": "application/x-www-form-urlencoded"}
)
print(f"Response: {resp.text}")

# 3. Fetch conversation history
resp = requests.get(
    f"{BASE_URL}/api/conversations/{user_id}/{conversation_id}"
)
history = resp.json()
for msg in history["messages"]:
    print(f"{msg['role'].upper()}: {msg['content'][:100]}...")
```

---

## Monitoring & Troubleshooting

### Check CosmosDB Activity

```bash
az cosmosdb show -g rg-<suffix> -n cosmos-<suffix> \
  --query "documentEndpoint" -o tsv
# Then view metrics in Azure Portal → CosmosDB account → Metrics
```

### View Container App Logs

```bash
az containerapp logs show \
  -g rg-<suffix> \
  -n query-web-ca \
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

### CosmosDB Throughput

For production workloads:

```bash
# Recommended: 800-1000 RU/s for active conversation store
az cosmosdb sql container throughput update \
  -a "cosmos-prod" \
  -g "rg-prod" \
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

### 1. RBAC for CosmosDB

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

- At rest: `enableCMKEncryption` on CosmosDB (optional)
- In transit: TLS 1.2+ enforced

---

## Rollback Plan

If issues arise:

1. **Disable conversation history** (keep Foundry API):
   - Remove `session_id` and `conversation_id` parameters from `/ask` form
   - App continues to function for single-turn queries

2. **Revert to previous requires.txt**:
   ```bash
   git checkout HEAD^ -- query_web/requirements.txt
   docker build -t query-web:rollback .
   az containerapp update --image query-web:rollback
   ```

3. **Preserve CosmosDB data**:
   - Conversation documents auto-persisted with partition key `/user_id`
   - Can be recovered if service restarted

---

## Next Steps

- Monitor conversation patterns and usage via CosmosDB metrics
- Implement conversation search (full-text search on message content)
- Add conversation export (download as Markdown)
- Set up alerts for CosmosDB throttling and error rates
