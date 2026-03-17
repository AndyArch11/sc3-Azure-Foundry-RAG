# Module: agent_hosting

Provisions the Azure Container Apps environment and (optionally) a Container
App Job for the ingestion runner.

## Resources

| Resource | Name pattern | Purpose |
|---|---|---|
| `azurerm_container_app_environment` | `cae-<suffix>` | VNet-integrated CAE on the agent-delegated subnet |
| `azurerm_container_app_job` | `caj-ingestion-<suffix>` | Manually-triggered ingestion pipeline job (created when `enable_ingestion_job = true`) |
| `azurerm_container_app` | `ca-rag-query-<suffix>` | Internal browser-accessible query web app (created when `enable_query_web_app = true`) |

The query web app provides:
- Hybrid retrieval (keyword + vector)
- Cyber-security persona prompt
- Evaluator scoring with one retry when score is below threshold
- Runtime controls for Top-K and temperature
- Optional shared-token auth gate via `query_web_auth_token`

## Networking

The environment is attached to `snet-container-apps` (dedicated and delegated
to `Microsoft.App/environments`, `10.20.5.0/24` in dev).  
`internal_load_balancer_enabled = true` means no public ingress — all traffic
is private-network only.

## Identity

The job runs as the user-assigned managed identity `id-agent-runtime-<suffix>`.
That identity has the following roles (managed by `modules/identity`):

- `Storage Blob Data Contributor` on the storage account
- `Search Index Data Contributor` on the Search service
- `Cognitive Services User` on the Foundry account
- `AcrPull` on the container registry

The same identity is attached to the jumpbox VM for interactive ingestion runs.

## Image

The job pulls from `<acr_login_server>/ingestion-runner:latest`.  
Build and push with:

```bash
ENV=dev IMAGE_TAG=latest ./ops/scripts/build-push-ingestion.sh
```

> **Note:** the script must run from inside the VNet (jumpbox or CI with VNet
> injection) because `public_network_access_enabled = false` on the ACR.

## Triggering

```bash
# Index files already in blob storage (default args)
az containerapp job start \
	-n caj-ingestion-dev-aue-001 \
	-g rg-ai-platform-dev

# Upload files and index in one step
az containerapp job start \
	-n caj-ingestion-dev-aue-001 \
	-g rg-ai-platform-dev \
	--args '--mode' 'azure' '--input-dir' '/path/to/files'

# Query web app endpoint (private ingress)
az containerapp show \
  -n ca-rag-query-dev-aue-001 \
  -g rg-ai-platform-dev \
  --query "properties.configuration.ingress.fqdn" -o tsv
```

## Inputs

See `variables.tf`. All inputs are wired from root `main.tf`.
