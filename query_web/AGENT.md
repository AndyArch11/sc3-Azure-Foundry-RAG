# AGENT.md — Query Web

## Scope

FastAPI query web app with Foundry chat, Azure Search hybrid retrieval, and Cosmos conversation persistence.

## Working Directory

query_web/

## Validation Before Container Rollout

```bash
python3 -m py_compile app.py
python3 -m pytest ../tests/unit/test_conversation_management.py -q
```

## Container Rollout (from Docker-capable host or jumpbox)

```bash
cd ..
TARGET_ENV="<env>"
IMAGE_TAG="$(date +%Y%m%d%H%M)-<gitsha>" ENV="${TARGET_ENV}" ./ops/scripts/build-push-query-web.sh

terraform -chdir=infra/terraform apply \
  -input=false \
  -var-file="environments/${TARGET_ENV}/bootstrap.generated.tfvars" \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars" \
  -var "query_web_image_tag=${IMAGE_TAG}" \
  -target=module.agent_hosting
```

## Environment Variables Required for Container App

- `AZURE_SEARCH_ENDPOINT`, `AZURE_OPENAI_ENDPOINT`, `AZURE_COSMOS_ENDPOINT`
- `AZURE_COSMOS_DATABASE_NAME`, `AZURE_COSMOS_CONTAINER_NAME`
- Optional: `QUERY_WEB_AUTH_TOKEN`

## Conversation Persistence Guardrails

- Document ID must be Cosmos-safe; avoid `#` and hyphens in UUID segments.
- User ID isolation: all queries and writes use partition_key=user_id.
- Persistence failures must surface in JSON error responses, not silent fallback.

## Code Changes That Require Image Rollout

- Cosmos interaction changes (partition key, document ID format).
- Foundry API call changes (parameter names, model versions).
- Message handling or persistence logic.
