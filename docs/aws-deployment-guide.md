# AWS Deployment Guide

This document covers the AWS equivalent of the Azure deployment runbook found in the
top-level README. The AWS stack replaces Azure-specific services with their nearest
AWS counterparts while preserving the same operational model and script conventions.

## Service Mapping

| Azure service / module  | AWS equivalent                          | Notes                                                              |
|-------------------------|-----------------------------------------|--------------------------------------------------------------------|
| Resource groups         | Tags (`project`, `environment`)         | AWS has no resource group construct                                |
| VNet + private endpoints| VPC + VPC interface/gateway endpoints   | Endpoints provisioned inside `module.network`                      |
| Azure Bastion + jumpbox | AWS Systems Manager Session Manager     | No EC2 bastion needed; use SSM only if you provision a separate EC2 admin host |
| Private DNS zones       | VPC Resolver (built-in)                 | No separate DNS module required                                    |
| Log Analytics Workspace | CloudWatch Log Groups                   | `module.observability` creates per-service log groups              |
| Prometheus metrics      | Amazon Managed Prometheus (AMP)         | ADOT sidecar remote-writes to the AMP workspace                    |
| Azure AI Search         | Amazon OpenSearch Service               | Provisioned inside `module.data_services`                          |
| Azure Cosmos DB         | Amazon DynamoDB                         | Conversation state store                                           |
| Azure Blob Storage      | Amazon S3                               | Grounding-data bucket                                              |
| Azure AI Foundry / OpenAI | Amazon Bedrock                        | Serverless; no dedicated module — configured via task env vars     |
| Azure Container Apps    | ECS Fargate                             | `module.app_hosting`                                               |
| Azure Application Gateway / Front Door (WAF) | Application Load Balancer (ALB) + enterprise WAF in front | `module.app_hosting` provisions ALB origin; WAF layer is external to this stack |
| App Service / Gateway TLS certificates | AWS Certificate Manager (ACM) + ALB HTTPS listener | `query_web_tls_certificate_arn` enables TLS on query-web ingress   |
| Azure Container Registry| Amazon ECR                              | `module.container_registry`                                        |
| Managed Identity (UAMI) | IAM task role + task execution role     | `module.identity`                                                  |
| Azure Key Vault         | AWS Secrets Manager                     | `module.app_secrets`; bootstrap optionally creates an auth token secret |
| Terraform state (blob)  | S3 + lockfile state locking             | `infra/terraform/aws/bootstrap`                                    |

## Repository Layout

AWS-specific paths in this repository:

```
infra/terraform/aws/
├── bootstrap/                  # Phase 0: S3 state bucket, DynamoDB lock table, optional Secrets Manager
├── environments/
│   ├── dev/dev.tfvars
│   ├── test/test.tfvars
│   └── prod/prod.tfvars
├── modules/
│   ├── network/                # VPC, subnets, NAT gateways, security groups, VPC endpoints
│   ├── observability/          # CloudWatch log groups, AMP workspace
│   ├── data_services/          # S3, OpenSearch Service, DynamoDB
│   ├── identity/               # ECS IAM task role + execution role
│   ├── container_registry/     # ECR repositories
│   ├── app_secrets/            # Secrets Manager runtime secrets
│   └── app_hosting/            # ECS cluster, Fargate task definitions and services
├── main.tf
├── variables.tf
├── outputs.tf
└── terraform.tfvars.example    # Copy to environments/<env>/<env>.tfvars
ops/scripts/aws/
├── phase1-bootstrap.sh         # Provision remote state backend
├── build-push-ingestion.sh     # Build + push ingestion image to ECR
├── build-push-query-web.sh     # Build + push query-web image to ECR
├── build-push-confluence-poller.sh # Build + push confluence-poller image to ECR
├── rollout-app-hosting.sh      # Targeted deploy: module.app_hosting + module.app_secrets
└── run-controls-task.sh        # Run one-off ECS task for control data ingestion
```

## Prerequisites

- AWS CLI ≥ 2 installed and on `PATH`
  - [AWS CLI Install](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- Terraform ≥ 1.6.0 on `PATH` (or use the Terraform runner container)
- Docker daemon running (required for image builds)
- AWS credentials with sufficient permissions to create VPCs, ECS, IAM roles, S3, OpenSearch, DynamoDB, ECR, and Secrets Manager resources

Authenticate before running any script:

```bash
# Interactive login (developer workstation)
aws configure
# or
# aws login

# Or export credentials directly
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="ap-southeast-2"

# If using AWS profiles/SSO, ensure Terraform can read shared config.
export AWS_SDK_LOAD_CONFIG=1

# Confirm identity
aws sts get-caller-identity
```

## Operator Checklist

1. Copy and customise `infra/terraform/aws/terraform.tfvars.example` to `infra/terraform/aws/environments/<env>/<env>.tfvars`.
2. Run `./ops/scripts/aws/phase1-bootstrap.sh <env>` to create remote state infrastructure.
3. Apply the main stack: `terraform -chdir=infra/terraform/aws apply -var-file=environments/<env>/<env>.tfvars`.
4. Build and push immutable image tags from a Docker-capable host.
5. Roll out those tags through `rollout-app-hosting.sh`.
6. Upload evidence and control data into S3 / OpenSearch, run the ingestion job, and load control data.
7. Run integration smoke tests against the query-web ECS service URL.

---

## Provision Infrastructure

### Set Target Environment

```bash
TARGET_ENV="dev"   # dev, test, or prod
AWS_REGION="ap-southeast-2"

export AWS_ACCESS_KEY_ID="ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="ACCESS_KEY"
export AWS_DEFAULT_REGION="ap-southeast-2"
```

### Phase 0: Bootstrap Remote State

Creates the S3 state bucket and bootstrap lock-table resources **once per environment**, and writes
`infra/terraform/aws/environments/${TARGET_ENV}/backend.hcl`:

```bash
./ops/scripts/aws/phase1-bootstrap.sh "${TARGET_ENV}"
```

Environment variable overrides:

| Variable                              | Default                              | Description                               |
|---------------------------------------|--------------------------------------|-------------------------------------------|
| `AWS_REGION`                          | `ap-southeast-2`                     | Target region                             |
| `TF_PROJECT`                          | `rag`                                | Project prefix used in resource naming    |
| `TF_BACKEND_KEY`                      | `aws/<env>/terraform.tfstate`        | S3 key for the main stack state file      |
| `TF_ENABLE_BOOTSTRAP_SECRETS_MANAGER` | `true`                               | Create a placeholder auth token secret    |

### Phase 1: Main Stack

```bash
terraform -chdir=infra/terraform/aws init \
  -reconfigure \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"

terraform -chdir=infra/terraform/aws plan -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars"
terraform -chdir=infra/terraform/aws apply -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars"
```

OpenSearch VPC domains require the AWS-managed service-linked role
`AWSServiceRoleForAmazonOpenSearchService`. This stack attempts to create it
when explicitly enabled (`ensure_opensearch_service_linked_role = true`).

OpenSearch log publishing also requires a CloudWatch Logs resource policy that
permits `es.amazonaws.com` to create streams and put events in
`/aws/opensearch/<suffix>`. This stack creates that policy automatically.

If your IAM policy blocks role discovery/creation, create it once with:

```bash
aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com
```

Then re-run apply. If needed, you can disable automatic role handling by setting:

```hcl
ensure_opensearch_service_linked_role = false
```

Recommendation: keep `ensure_opensearch_service_linked_role = false` for normal
runs, and set it to `true` only during first-time account bootstrap when the role
is known to be absent.

If your organisation blocks Terraform from managing CloudWatch Logs resource
policies, create one manually (adjust account, region, and log group):

```bash
aws logs put-resource-policy \
  --policy-name "opensearch-log-publish" \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Sid":"AllowOpenSearchServiceToPublishLogs",
      "Effect":"Allow",
      "Principal":{"Service":"es.amazonaws.com"},
      "Action":["logs:CreateLogStream","logs:PutLogEvents"],
      "Resource":[
        "arn:aws:logs:ap-southeast-2:<account-id>:log-group:/aws/opensearch/rag-dev-apse2",
        "arn:aws:logs:ap-southeast-2:<account-id>:log-group:/aws/opensearch/rag-dev-apse2:*"
      ]
    }]
  }'
```

If apply fails with `Unable to assume the service linked role` on
`aws_ecs_cluster_capacity_providers`, use the current module defaults (capacity
provider attachment is disabled by default). If you intentionally enable capacity
providers, create the ECS service-linked role first:

```bash
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com
```

Or using the Terraform runner container:

```bash
cd infra/terraform/aws
docker build -t tf-runner:local ops/containers/terraform-runner

docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  -v "$(pwd)":/workspace \
  tf-runner:local \
  terraform -chdir=/workspace/infra/terraform/aws \
    apply -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars"
```

Key outputs after apply:

```bash
terraform -chdir=infra/terraform/aws output opensearch_endpoint
terraform -chdir=infra/terraform/aws output query_web_repository_url
terraform -chdir=infra/terraform/aws output ingestion_repository_url
terraform -chdir=infra/terraform/aws output amp_remote_write_url
```

### Customising the tfvars file

Start from the example:

```bash
cp infra/terraform/aws/terraform.tfvars.example \
   infra/terraform/aws/environments/${TARGET_ENV}/${TARGET_ENV}.tfvars
```

Key variables to review:

| Variable                    | Example                                         | Description                                     |
|-----------------------------|-------------------------------------------------|-------------------------------------------------|
| `aws_region`                | `ap-southeast-2`                                | Target region                                   |
| `aws_region_short`          | `apse2`                                         | Short code used in resource names               |
| `project`                   | `rag`                                           | Name prefix                                     |
| `environment`               | `dev`                                           | Environment label                               |
| `vpc_cidr`                  | `10.30.0.0/16`                                  | VPC CIDR                                        |
| `private_subnet_cidrs`      | `["10.30.1.0/24", "10.30.2.0/24"]`              | Private subnets (minimum 2, one per AZ)         |
| `public_subnet_cidrs`       | `["10.30.101.0/24", "10.30.102.0/24"]`          | Public subnets for NAT gateways                 |
| `opensearch_instance_type`  | `r6g.large.search`                              | Resize for production workloads                 |
| `opensearch_instance_count` | `1`                                             | Increase for HA                                 |
| `ensure_opensearch_service_linked_role` | `false`                                | Opt-in creation of OpenSearch service-linked role for first bootstrap |
| `query_web_ingress_mode`   | `auto` / `internal` / `public` / `none`    | `auto` resolves to `internal` in prod and `none` elsewhere |
| `query_web_public_ingress_cidrs` | ` ["203.0.113.0/24"] `             | Required only when `query_web_ingress_mode = "public"` |
| `query_web_tls_certificate_arn` | `arn:aws:acm:...:certificate/...`     | Optional ACM certificate ARN to enable HTTPS on the ALB |
| `query_web_tls_ssl_policy` | `ELBSecurityPolicy-TLS13-1-2-2021-06`       | Optional HTTPS listener SSL policy |
| `bedrock_model_id`          | `anthropic.claude-3-5-sonnet-20241022-v2:0`     | Inference model ID; ensure model is available in the target region |
| `bedrock_embedding_model_id`| `amazon.titan-embed-text-v2:0`                  | Embedding model                                 |
| `bedrock_api_mode`          | `runtime`                                       | Bedrock API path: `runtime` (IAM SigV4) or `mantle` (API key) |
| `query_web_image_tag`       | `latest`                                        | ECR tag; update after each push                 |
| `ingestion_image_tag`       | `latest`                                        | ECR tag; update after each push                 |

> [!IMPORTANT]
> Trial/new AWS accounts may not be able to run Bedrock LLM inference immediately.
> In some accounts, applied Bedrock runtime quotas are set to `0` (for example,
> requests per minute and tokens per minute), which causes all inference calls to fail
> even when AWS default quotas are non-zero. If this happens, open an AWS Support case
> and request Bedrock on-demand runtime access/quota activation for your account and region.

`query_web_auth_token` is no longer a Terraform variable. Query-web auth is sourced from
Secrets Manager (`auth_token` field in `app/<project>-<env>-<aws_region_short>`).

If `bedrock_api_mode = "mantle"`, ECS tasks read `BEDROCK_API_KEY` from the same
application secret (`bedrock_api_key` field).

Ingress behaviour:

- `query_web_ingress_mode = "auto"` gives production an internal ALB and leaves non-prod with no ALB by default.
- `query_web_ingress_mode = "internal"` creates a private ALB in the private subnets.
- `query_web_ingress_mode = "public"` creates an internet-facing ALB, but only for non-prod and only when `query_web_public_ingress_cidrs` is set.
- `query_web_ingress_mode = "none"` leaves the ECS service private-only with no ALB.
- Set `query_web_tls_certificate_arn` to add an HTTPS listener on port 443. When TLS is enabled, the ALB keeps port 80 only to redirect HTTP requests to HTTPS.
- In an enterprise deployment, treat the ALB as the HTTPS origin behind a WAF. The WAF is expected to own the stable DNS name; this stack does not need to create Route 53 records before HTTPS can be used.

> **Bedrock serverless models:** Manual activation in **Amazon Bedrock → Model access** is retired for AWS commercial regions.
> Serverless foundation models are enabled on first invocation. Before applying, verify that:
> 1. your selected model IDs are available in the target region, and
> 2. your account has non-zero Bedrock runtime quotas in that region.

For AWS runtime issues (Bedrock access/quota, ECS task role permissions, ingestion auth),
see [AWS-Specific Troubleshooting Tips](troubleshoot.md#aws-specific-troubleshooting-tips).

---

## Build and Push Images

Build and push from a Docker-capable host that is authenticated to AWS. ECR does not
require private network access for push when VPC endpoints are absent — standard HTTPS
to the ECR public endpoint is sufficient.

Terraform creates ECR repositories under the naming suffix path, for example:

- `${project}-${environment}-${aws_region_short}/ingestion`
- `${project}-${environment}-${aws_region_short}/query-web`
- `${project}-${environment}-${aws_region_short}/confluence-poller`

The build scripts first read Terraform outputs (`ingestion_repository_url` and
`query_web_repository_url`). If outputs are unavailable, they derive the same path
from `project`, `environment`, and `aws_region_short` in the environment tfvars.

### Ingestion image

```bash
ENV="${TARGET_ENV}" \
IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git rev-parse --short HEAD)" \
./ops/scripts/aws/build-push-ingestion.sh
```

### Query-web image

```bash
ENV="${TARGET_ENV}" \
IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git rev-parse --short HEAD)" \
./ops/scripts/aws/build-push-query-web.sh
```

### Confluence poller image

```bash
ENV="${TARGET_ENV}" \
IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git rev-parse --short HEAD)" \
./ops/scripts/aws/build-push-confluence-poller.sh
```

After each push, update the corresponding `*_image_tag` value in `environments/${TARGET_ENV}/${TARGET_ENV}.tfvars` with the immutable tag produced above.

---

## Roll Out App Services

Use `rollout-app-hosting.sh` for all image-tag rollouts. This script targets only
`module.app_hosting` and `module.app_secrets`, supports the Confluence poller, and waits for ECS service stability:

```bash
# Roll out query-web and ingestion
./ops/scripts/aws/rollout-app-hosting.sh "${TARGET_ENV}" apply \
  --query-web-tag "<immutable-query-web-tag>" \
  --ingestion-tag "<immutable-ingestion-tag>"

# Enable and roll out the Confluence poller
./ops/scripts/aws/rollout-app-hosting.sh "${TARGET_ENV}" apply \
  --confluence-poller-tag "<immutable-confluence-poller-tag>" \
  --enable-confluence-poller \
  --confluence-base-url "https://<org>.atlassian.net" \
  --confluence-auth-mode basic \
  --confluence-auth-email "service-account@example.com" \
  --confluence-api-token "<token>"

# Preview changes only (no apply)
./ops/scripts/aws/rollout-app-hosting.sh "${TARGET_ENV}" plan \
  --query-web-tag "<immutable-query-web-tag>"

# Skip ECS stabilisation wait (faster CI)
./ops/scripts/aws/rollout-app-hosting.sh "${TARGET_ENV}" apply \
  --query-web-tag "<immutable-query-web-tag>" \
  --no-wait
```

For the Confluence poller, advanced tuning such as `confluence_poll_space_keys`,
`confluence_poll_interval_seconds`, and `confluence_poll_dry_run` can live in tfvars,
or be overridden at rollout time where supported.

---

## Load Control Data

For AWS, run controls ingestion as a one-off ECS Fargate task inside the VPC using the provided helper script.

The helper script automatically resolves all Terraform outputs and constructs the proper `aws ecs run-task` invocation:

```bash
# Parse and publish AESCSF controls, waiting for completion
./ops/scripts/aws/run-controls-task.sh aescsf --env dev --wait

# Parse and publish all supported frameworks with replace mode
./ops/scripts/aws/run-controls-task.sh all --env dev --replace-existing --wait

# Dry-run dedupe/publish decision (parse only, no publish)
./ops/scripts/aws/run-controls-task.sh essential_eight --env dev --dry-run
```

Supported frameworks: `aescsf`, `all`, `cis_controls`, `essential_eight`, `ism`, `nist_ai_rmf`, `nist_csf`, `pci_dss`, `pspf`.

Other options:
- `--replace-existing` — Replace existing controls in OpenSearch (default: deduplicate)
- `--dry-run` — Parse controls but do not publish
- `--no-guidance` — Omit guidance text to reduce payload size
- `--wait` — Wait for task completion and stream CloudWatch logs to terminal
- `--env <name>` — Target environment (default: dev)

For full usage:

```bash
./ops/scripts/aws/run-controls-task.sh --help
```

Notes:

- `cis_controls` and `pci_dss` still require operator-supplied source documents first, as described in [runtime/README.md](runtime/README.md).
- The task definition already includes the AWS-specific environment variables (`CLOUD_PROVIDER=aws`, `OPENSEARCH_ENDPOINT`, `CONTROLS_INDEX_NAME`, `AWS_REGION`).
- The helper now performs a preflight check against ECR before task launch and fails fast if the ingestion image tag in the task definition does not exist.
- Without `--wait`, monitor task progress in the ECS console or with: `aws ecs describe-tasks --cluster <cluster> --tasks <task-id> --region <region>`.
- View task logs: `aws logs tail /rag/<env>/ingestion --follow`.

---

## Validate the Deployment

The AWS stack optionally provisions an ALB for `query-web`.

- In `prod`, `query_web_ingress_mode = "auto"` resolves to an internal ALB.
- In non-prod, `query_web_ingress_mode = "auto"` resolves to `none` by default.
- For sandbox-style browser access in non-prod, set `query_web_ingress_mode = "public"`
  and provide `query_web_public_ingress_cidrs`.
- Set `query_web_tls_certificate_arn` if you want `query_web_url` to resolve to `https://...` instead of `http://...`.

Validate the deployment in two stages:

1. Confirm the service task is healthy inside ECS.
2. If ingress is enabled, use the `query_web_url` Terraform output as the base URL
  for browser access and HTTP integration tests.
3. If ingress is disabled, run HTTP validation only from a host that already has
  private network reachability to the task endpoint or via ECS Exec / port forwarding.

Example health validation via ECS Exec:

```bash
CLUSTER="$(terraform -chdir=infra/terraform/aws output -raw ecs_cluster_name)"
QUERY_WEB_SERVICE="$(terraform -chdir=infra/terraform/aws output -raw query_web_service_name)"
TASK_ID="$(aws ecs list-tasks --cluster "${CLUSTER}" --service-name "${QUERY_WEB_SERVICE}" --query 'taskArns[0]' --output text)"

aws ecs execute-command \
  --cluster "${CLUSTER}" \
  --task "${TASK_ID}" \
  --container query-web \
  --interactive \
  --command "curl -sf http://localhost:8080/health"
```

If ingress is enabled, run the query-web integration tests against the provisioned
query-web URL:

```bash
QUERY_WEB_BASE_URL="$(terraform -chdir=infra/terraform/aws output -raw query_web_url 2>/dev/null || echo '')"

# If query-web auth is enabled, retrieve the shared app token from Secrets Manager.
# Secret naming follows: app/<project>-<env>-<aws_region_short>
QUERY_WEB_AUTH_TOKEN="$(aws secretsmanager get-secret-value \
  --secret-id "app/rag-${TARGET_ENV}-apse2" \
  --query 'SecretString' \
  --output text | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("auth_token", ""))')"

QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=false \
QUERY_WEB_REQUIRE_API_ASK=false \
./ops/scripts/azure/run-query-web-integration-tests.sh \
  "${QUERY_WEB_BASE_URL}" \
  "${QUERY_WEB_AUTH_TOKEN}"
```

For AWS deployments, conversation persistence depends on Azure CosmosDB and is
typically unavailable; keep `QUERY_WEB_REQUIRE_CONVERSATIONS=false` unless you
have explicitly enabled a compatible persistence backend.

`QUERY_WEB_REQUIRE_API_ASK` controls strictness for `/api/ask` checks:

- `false` (recommended default): skip `/api/ask` assertions when the endpoint
  returns a deployment-specific internal error.
- `true`: fail the suite if `/api/ask` returns an error.

> The integration test script is cloud-agnostic, but it still requires a real HTTP
> base URL that is reachable from the machine running the tests.

For HTTPS, the ACM certificate must already exist in the same region as the ALB.
For public ingress, that usually means a certificate covering the public DNS name you
intend to use. For internal-only ingress, use a certificate that matches the internal
DNS name clients will actually connect to.

If the long-term entry point will be a WAF, validate in two phases:

1. First validate the ALB origin directly using `query_web_url` after TLS is enabled.
2. Then validate the final WAF hostname after the WAF policy, DNS, and forwarding rules are in place.

If `echo "$QUERY_WEB_BASE_URL"` prints an empty string, ingress is currently disabled.
Either enable `query_web_ingress_mode` or continue validating from inside the VPC via
ECS Exec, SSM-connected hosts, or your own private access path.

If `echo "$QUERY_WEB_AUTH_TOKEN"` prints `<set-me>`, the secret is still using the
bootstrap placeholder and must be replaced before browser sign-in or integration
tests will succeed.

If `bedrock_api_mode = "mantle"`, ensure `bedrock_api_key` is also populated in the
same secret before restarting services.

Generate and set a real token:

```bash
TOKEN="$(openssl rand -base64 32)"

aws secretsmanager put-secret-value \
  --secret-id "app/rag-${TARGET_ENV}-apse2" \
  --secret-string "{\"auth_token\":\"${TOKEN}\"}"
```

Set both query-web auth token and Bedrock Mantle API key in one update:

```bash
AUTH_TOKEN="$(openssl rand -base64 32)"
BEDROCK_KEY="<your-short-term-bedrock-api-key>"

aws secretsmanager put-secret-value \
  --secret-id "app/rag-${TARGET_ENV}-apse2" \
  --secret-string "{\"auth_token\":\"${AUTH_TOKEN}\",\"bedrock_api_key\":\"${BEDROCK_KEY}\"}"
```

ECS injects Secrets Manager values only at task start, so restart or roll out the
query-web service after changing the secret:

```bash
./ops/scripts/aws/rollout-app-hosting.sh "${TARGET_ENV}" apply \
  --query-web-tag "<current-query-web-tag>"
```

If query-web `/ask` fails with an internal error and CloudWatch logs show
`401 Unauthorized` from Bedrock Mantle (for example,
`https://bedrock-mantle.<region>.api.aws/v1/chat/completions`), treat the
`bedrock_api_key` as expired/invalid and rotate it in Secrets Manager, then
roll out query-web so new ECS tasks load the updated value.

For Bedrock Mantle OpenAI-compatible calls, send bearer auth only
(`Authorization: Bearer <key>`). If a request includes both `Authorization`
and `x-api-key` headers, Mantle returns `401 invalid_api_key`.

If you open the site in a browser and see a sign-in prompt, that is not asking for
AWS credentials. The app is rejecting the request because query-web shared-token auth
is enabled and expects the `auth_token` stored in Secrets Manager. Use the token above
for integration tests, or update the secret value if the currently configured token is
not the one you expect.

To open the browser with the auth token pre-filled, URL-encode the token first:

```bash
ENCODED_TOKEN="$(python3 -c 'import urllib.parse,os; print(urllib.parse.quote(os.environ.get("QUERY_WEB_AUTH_TOKEN", ""), safe=""))')"
echo "${QUERY_WEB_BASE_URL}/?auth_token=${ENCODED_TOKEN}"
```

Validate the token works before opening in a browser:

```bash
curl -sS -o qw_home.html -w "http_code=%{http_code}\n" \
  --get --data-urlencode "auth_token=${QUERY_WEB_AUTH_TOKEN}" \
  "${QUERY_WEB_BASE_URL}/"
```

Expected: `http_code=200` and `qw_home.html` contains the RAG Query Console HTML.
If you see `http_code=401` and "Unauthorised", the token in Secrets Manager does not match the one you are passing.

### Confluence Poller Validation

After enabling the poller, confirm the ECS service exists and is stable:

```bash
terraform -chdir=infra/terraform/aws output -raw confluence_poller_service_name
```

Inspect recent Confluence poller logs:

```bash
aws logs tail "/ecs/rag-${TARGET_ENV}-apse2/confluence-poller" --follow
```

Expected steady-state behaviour:

- the service remains at one running task
- logs show repeated poll cycles rather than one-shot exit behaviour
- successful cycles advance the `confluence` watermark in the DynamoDB orchestration table

If you supplied `--confluence-api-token` during rollout, the script updates the
`confluence_api_token` field in Secrets Manager and forces a fresh poller deployment so
new tasks pick up the rotated token.

### HTTPS Validation

Once `query_web_tls_certificate_arn` is set and Terraform has been applied, validate the
HTTPS listener and redirect behaviour directly against the ALB origin first.

Check that the output now resolves to `https://...`:

```bash
terraform -chdir=infra/terraform/aws output -raw query_web_url
```

Validate the HTTPS health endpoint using the ACM-backed listener:

```bash
QUERY_WEB_BASE_URL="$(terraform -chdir=infra/terraform/aws output -raw query_web_url)"

curl --fail --silent --show-error \
  "${QUERY_WEB_BASE_URL}/health"
```

Validate that plain HTTP redirects to HTTPS:

```bash
QUERY_WEB_LB_DNS_NAME="$(terraform -chdir=infra/terraform/aws output -raw query_web_lb_dns_name)"

curl --silent --show-error --output /dev/null \
  --write-out '%{http_code} %{redirect_url}\n' \
  "http://${QUERY_WEB_LB_DNS_NAME}/health"
```

Expected result: `301 https://.../health`

Run the integration test suite over HTTPS:

```bash
QUERY_WEB_BASE_URL="$(terraform -chdir=infra/terraform/aws output -raw query_web_url)"
QUERY_WEB_AUTH_TOKEN="$(aws secretsmanager get-secret-value \
  --secret-id "app/rag-${TARGET_ENV}-apse2" \
  --query 'SecretString' \
  --output text | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("auth_token", ""))')"

QUERY_WEB_RUN_API_ASK=true \
QUERY_WEB_REQUIRE_CONVERSATIONS=false \
QUERY_WEB_REQUIRE_API_ASK=false \
./ops/scripts/azure/run-query-web-integration-tests.sh \
  "${QUERY_WEB_BASE_URL}" \
  "${QUERY_WEB_AUTH_TOKEN}"
```

After the enterprise WAF is in front of the ALB, repeat the same checks against the WAF
hostname. At that point the WAF-managed DNS name becomes the canonical user-facing URL,
while `query_web_url` remains the direct origin URL for operator validation.

---

## Private Network Access

The AWS stack does not provision a bastion host. Use `aws ecs execute-command` for interactive access into running ECS tasks.

Example ECS Exec flow for the running query-web service:

```bash
CLUSTER="$(terraform -chdir=infra/terraform/aws output -raw ecs_cluster_name)"
QUERY_WEB_SERVICE="$(terraform -chdir=infra/terraform/aws output -raw query_web_service_name)"
TASK_ID="$(aws ecs list-tasks --cluster "${CLUSTER}" --service-name "${QUERY_WEB_SERVICE}" --query 'taskArns[0]' --output text)"

aws ecs execute-command \
  --cluster "${CLUSTER}" \
  --task "${TASK_ID}" \
  --container query-web \
  --interactive \
  --command "/bin/sh"
```

ECS Exec requires ECS Exec to be enabled for the running task/service and the task IAM role to include `ssmmessages:*` permissions. The current stack includes the IAM permissions; if you want interactive shell access as a first-class operational path, enable ECS Exec on the relevant ECS service/task definition rollout.

---

## Observability

The `module.observability` output provides an AMP workspace. To forward metrics from
the query-web ECS task, configure an ADOT sidecar with the remote-write URL:

```bash
AMP_REMOTE_WRITE_URL="$(terraform -chdir=infra/terraform/aws output -raw amp_remote_write_url)"
```

Use the standard CloudWatch log group names for log routing:

- `/rag/<env>/query-web`
- `/rag/<env>/ingestion`

Log queries use CloudWatch Log Insights; the same logql patterns from the local Loki
setup translate directly — filter on `correlation_id`, `trace_id`, or `level`.

---

## Uninstall

```bash
# 1. Destroy the main stack
terraform -chdir=infra/terraform/aws destroy \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars"

# 2. Empty the S3 state bucket (versioned objects must be deleted before destroy)
STATE_BUCKET="$(terraform -chdir=infra/terraform/aws/bootstrap output -raw state_bucket_name)"
aws s3 rm "s3://${STATE_BUCKET}" --recursive
aws s3api delete-objects \
  --bucket "${STATE_BUCKET}" \
  --delete "$(aws s3api list-object-versions \
    --bucket "${STATE_BUCKET}" \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
    --output json)"

# 3. Destroy bootstrap (removes state bucket and DynamoDB lock table)
terraform -chdir=infra/terraform/aws/bootstrap destroy
```

> The S3 bucket and DynamoDB table carry `prevent_destroy = true` lifecycle guards.
> Remove them from the Terraform source before the final bootstrap destroy if Terraform
> refuses to delete them.

---

## Common Commands

```bash
TARGET_ENV="dev"

# Required for profile/SSO-based credentials with Terraform.
export AWS_SDK_LOAD_CONFIG=1

# Authenticate
aws sts get-caller-identity

# Bootstrap remote state
./ops/scripts/aws/phase1-bootstrap.sh "${TARGET_ENV}"

# Apply full stack
terraform -chdir=infra/terraform/aws init \
  -backend-config="environments/${TARGET_ENV}/backend.hcl"
terraform -chdir=infra/terraform/aws apply \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars"

# Build and push images
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git rev-parse --short HEAD)" \
  ./ops/scripts/aws/build-push-ingestion.sh
ENV="${TARGET_ENV}" IMAGE_TAG="$(date +%Y%m%d%H%M)-$(git rev-parse --short HEAD)" \
  ./ops/scripts/aws/build-push-query-web.sh

# Roll out app hosting
./ops/scripts/aws/rollout-app-hosting.sh "${TARGET_ENV}" apply \
  --query-web-tag "<immutable-query-web-tag>" \
  --ingestion-tag "<immutable-ingestion-tag>"

# Parse and publish control data via a one-off ECS task
./ops/scripts/aws/run-controls-task.sh aescsf --env dev --wait

# Run unit tests
python3 -m pytest tests/unit -q

# Destroy
terraform -chdir=infra/terraform/aws destroy \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars"
```
