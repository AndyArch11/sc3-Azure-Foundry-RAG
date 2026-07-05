# Gateway Integration Deployment Option

This option publishes a stable external API contract without changing the core query-web deployment.

## Goal

- Keep query-web private on internal networking.
- Authenticate external callers at an edge gateway.
- Inject query-web shared token at the gateway boundary.
- Forward requests to query-web `/api/ask` using the published service contract.

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
6. Backend: private query-web origin URL.

## Option B: AWS API Gateway (recommended for AWS consumers)

1. Import [docs/contracts/rag-api-v1.openapi.yaml](docs/contracts/rag-api-v1.openapi.yaml).
2. Use IAM SigV4 or JWT authorizer for caller authentication.
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
   - Forward to local query-web `/api/ask` backend.
6. For Docker Compose, run the gateway as a separate service so this remains an additive option.

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

            location = /api/ask {
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
      image: openresty/openresty:alpine
      container_name: rag-gateway-local
      ports:
         - "18081:8081"
      environment:
         - QUERY_WEB_AUTH_TOKEN=${QUERY_WEB_AUTH_TOKEN}
      volumes:
         - ./ops/local-gateway/nginx.conf:/usr/local/openresty/nginx/conf/nginx.conf:ro
      depends_on:
         - query-web
```

Callers then target `http://localhost:18081/api/ask` and do not send `auth_token` directly.

## Security model

- External auth is enforced by APIM/API Gateway/local gateway.
- Internal app auth is still enforced by query-web shared token.
- Token rotation is independent from caller identity and should happen in Key Vault/Secrets Manager or local secret management.

## Operational notes

- Treat this as an additional deployment option, not a replacement for existing web deployment scripts.
- Keep API contract versioned (`v1`, `v2`) and avoid breaking schema changes in place.
- Add automated smoke tests that call the gateway endpoint and assert `/api/ask` response shape.
