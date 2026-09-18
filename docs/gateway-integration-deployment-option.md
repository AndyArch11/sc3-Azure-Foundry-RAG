# Gateway Integration Deployment Option

This option publishes a stable external API contract without changing the core query-web deployment.

## Goal

- Keep query-web private on internal networking.
- Authenticate external callers at an edge gateway.
- Inject query-web shared token at the gateway boundary.
- Forward requests to query-web `/api/v1/ask` using the published service contract.

Contract reference: [docs/contracts/rag-api-v1.openapi.yaml](docs/contracts/rag-api-v1.openapi.yaml)

## Option A: Azure APIM (recommended for Azure consumers)

1. Publish [docs/contracts/rag-api-v1.openapi.yaml](docs/contracts/rag-api-v1.openapi.yaml) as API version `v1`.
2. Protect APIM with Entra app auth (client credentials).
3. Use a user-assigned managed identity for APIM access to Key Vault.
4. Store query-web shared token in Key Vault.
5. Inbound policy:
   - Validate JWT issuer/audience.
   - Read shared token from Key Vault-backed named value.
   - Set or overwrite request body field `auth_token` before backend call.
6. Rewrite the published `/api/v1/...` paths to the private query-web `/api/...` paths.
7. Backend: private query-web origin URL.

For APIM MCP exposure, publish the read-only graph operations as tools from the OpenAPI contract:

- `getGraphNode`
- `getGraphRelated`
- `queryGraph`
- `getGraphExport` only when bounded export is an approved agent capability

The `queryGraph` operation is the preferred agent graph tool because it supports bounded seed traversal,
framework/node/edge/community filters, confidence filtering, and explicit result limits. Do not publish
`POST /api/graph/build` as an MCP tool. APIM should inject the internal `auth_token` after validating the
agent identity; callers and tool schemas should never provide that field.

## Option B: AWS API Gateway (recommended for AWS consumers)

1. Import [docs/contracts/rag-api-v1.openapi.yaml](docs/contracts/rag-api-v1.openapi.yaml).
2. Use IAM SigV4 or JWT authoriser for caller authentication.
3. Store query-web shared token in Secrets Manager.
4. Integration mapping:
   - Inject `auth_token` into JSON body.
   - Forward to private query-web ALB origin.
5. Restrict query-web origin network path to API Gateway/VPC path.

## Option C: Local Gateway (recommended for local integration testing)

1. Import [docs/contracts/rag-api-v1.openapi.yaml](docs/contracts/rag-api-v1.openapi.yaml) into your local gateway tool (for example Kong, Envoy, or Nginx with route rules).
2. Keep query-web on the local/private network path only.
3. Configure caller auth at the gateway (for example static bearer token, mTLS, or local OIDC emulator).
4. Store query-web shared token as a local secret or environment variable.
5. Gateway request transform:
   - Inject `auth_token` into JSON request body.
   - Forward to local query-web `/api/ask` backend from external `/api/v1/ask`.
6. For Docker Compose, run the gateway as a separate service so this remains an additive option.

### Repository quick start (local)

1. Set local gateway env values in `.env.local`:
    - `RAG_GATEWAY_HOST_PORT=18081`
    - `GATEWAY_BEARER_TOKEN=<optional-edge-token>`
    - `QUERY_WEB_AUTH_TOKEN=<query-web-shared-token-or-empty>`
2. Start local stack with gateway profile:

```bash
docker compose \
   -f docker-compose.local.yml \
   --env-file .env.local \
   --profile gateway \
   up --build
```

3. Verify gateway and contract publication:

```bash
curl -fsS http://localhost:18081/gateway/health
curl -fsS http://localhost:18081/openapi/v1/rag-api-v1.openapi.yaml | head -n 5
```
If running commands from a managed Docker container, use
`http://host.docker.internal:18081/` rather than `http://localhost:18081`.
The gateway smoke script tries the localhost URL first and automatically falls
back to the equivalent `host.docker.internal` URL when loopback is unavailable.

4. Run smoke test through gateway path:

```bash
ops/scripts/local/smoke-local-gateway-ask.sh
```

The local gateway also exposes the bounded graph tool at
`POST /api/v1/graph/query`, forwarding to query-web `/api/graph/query` with the
same bearer validation and internal token injection used by the Ask route.
Agents should use the gateway URL (`http://localhost:18081` by default), not the
`rag-query-web-local` container directly.

5. If `GATEWAY_BEARER_TOKEN` is set, callers must include:

```text
Authorization: Bearer <GATEWAY_BEARER_TOKEN>
```

### Local OpenResty example

This example keeps query-web unchanged and injects `auth_token` at the gateway.

`nginx.conf`:

```nginx
worker_processes 1;

events {
      worker_connections 1024;
}

http {
      lua_package_path "/usr/local/openresty/lualib/?.lua;;";

      server {
            listen 8081;

            location = /health {
                  proxy_pass http://query-web:8080/health;
            }

            location = /api/v1/ask {
                  content_by_lua_block {
                        ngx.req.read_body()
                        local cjson = require("cjson.safe")
                        local body = ngx.req.get_body_data() or "{}"
                        local payload = cjson.decode(body) or {}

                        payload.auth_token = os.getenv("QUERY_WEB_AUTH_TOKEN") or ""

                        local new_body = cjson.encode(payload)
                        ngx.req.set_body_data(new_body)
                        ngx.req.set_header("Content-Type", "application/json")
                        ngx.req.set_header("Content-Length", #new_body)

                        return ngx.exec("@ask_backend")
                  }
            }

            location @ask_backend {
                  proxy_pass http://query-web:8080/api/ask;
            }
      }
}
```

`docker-compose.override.yml` (example):

```yaml
services:
   rag-gateway-local:
      build:
         context: .
         dockerfile: ops/local-gateway/Dockerfile
      container_name: rag-gateway-local
      ports:
         - "18081:8081"
      environment:
         - QUERY_WEB_AUTH_TOKEN=${QUERY_WEB_AUTH_TOKEN}
      depends_on:
         - query-web
```

Callers then target `http://localhost:18081/api/v1/ask` and do not send `auth_token` directly.

## Security model

- External auth is enforced by APIM/API Gateway/local gateway.
- Internal app auth is still enforced by query-web shared token.
- Token rotation is independent from caller identity and should happen in Key Vault/Secrets Manager or local secret management.

## Operational notes

- Treat this as an additional deployment option, not a replacement for existing web deployment scripts.
- Keep API contract versioned (`v1`, `v2`) and avoid breaking schema changes in place.
- Add automated smoke tests that call the gateway endpoint and assert `/api/v1/ask` response shape.
