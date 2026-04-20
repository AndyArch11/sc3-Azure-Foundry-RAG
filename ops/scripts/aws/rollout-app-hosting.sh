#!/usr/bin/env bash
# Deploy or update the ECS app_hosting module (query-web service + ingestion task definition).
#
# Usage:
#   ./ops/scripts/aws/rollout-app-hosting.sh <env> [plan|apply] [options]
#
# This script:
#   - targets module.app_hosting and module.app_secrets only
#   - optionally overrides query-web and ingestion image tags at apply time
#   - waits for the ECS service to reach a stable state after apply
#   - does NOT touch data services, networking, or identity modules
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/aws/rollout-app-hosting.sh <env> [plan|apply] [--query-web-tag <tag>] [--ingestion-tag <tag>] [--no-wait]

Runs a targeted Terraform plan/apply for module.app_hosting and module.app_secrets only.

Defaults:
  env    = dev
  action = apply

Options:
  --query-web-tag <tag>    Override query_web_image_tag at apply time
  --ingestion-tag <tag>    Override ingestion_image_tag at apply time
  --no-wait                Skip waiting for ECS service stabilisation after apply

Examples:
  ./ops/scripts/aws/rollout-app-hosting.sh dev apply --query-web-tag 202604201200-abc1234
  ./ops/scripts/aws/rollout-app-hosting.sh dev apply --ingestion-tag 202604201200-abc1234
  ./ops/scripts/aws/rollout-app-hosting.sh prod plan --query-web-tag 202604201200-abc1234
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/aws"

ENVIRONMENT="${1:-dev}"
ACTION="${2:-apply}"

shift $(( $# >= 1 ? 1 : 0 ))
shift $(( $# >= 1 ? 1 : 0 ))

case "${ENVIRONMENT}" in
  dev|test|prod) ;;
  *)
    echo "Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod." >&2
    exit 1
    ;;
esac

case "${ACTION}" in
  plan|apply) ;;
  *)
    echo "Unsupported action '${ACTION}'. Use one of: plan, apply." >&2
    exit 1
    ;;
esac

QUERY_WEB_TAG=""
INGESTION_TAG=""
NO_WAIT="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query-web-tag)
      QUERY_WEB_TAG="${2:-}"
      shift 2
      ;;
    --ingestion-tag)
      INGESTION_TAG="${2:-}"
      shift 2
      ;;
    --no-wait)
      NO_WAIT="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Use --help for usage." >&2
      exit 1
      ;;
  esac
done

BACKEND_FILE="${TF_DIR}/environments/${ENVIRONMENT}/backend.hcl"
VAR_FILE="${TF_DIR}/environments/${ENVIRONMENT}/${ENVIRONMENT}.tfvars"

if ! command -v terraform >/dev/null 2>&1; then
  echo "ERROR: terraform is required in PATH." >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is required in PATH." >&2
  exit 1
fi

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not authenticated." >&2
  echo "Run: aws configure  (or configure an instance profile / OIDC)" >&2
  exit 1
fi

if [[ ! -f "${BACKEND_FILE}" ]]; then
  echo "ERROR: Backend config not found at ${BACKEND_FILE}." >&2
  echo "Run ./ops/scripts/aws/phase1-bootstrap.sh ${ENVIRONMENT} first." >&2
  exit 1
fi

echo "==> Initialising Terraform (${ENVIRONMENT})"
terraform -chdir="${TF_DIR}" init \
  -reconfigure \
  -input=false \
  -backend-config="${BACKEND_FILE}"

EXTRA_VAR_ARGS=()
if [[ -n "${QUERY_WEB_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=query_web_image_tag=${QUERY_WEB_TAG}")
fi
if [[ -n "${INGESTION_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=ingestion_image_tag=${INGESTION_TAG}")
fi

echo "==> Running ${ACTION} (module.app_hosting, module.app_secrets)"

TF_ACTION_ARGS=(
  -input=false
  -var-file="${VAR_FILE}"
)

if [[ "${ACTION}" == "plan" ]]; then
  # Skip full state refresh on plan for speed; the targeted modules contain no
  # data sources that require a live lookup.
  TF_ACTION_ARGS+=(-refresh=false)
fi

TF_ACTION_ARGS+=(
  "${EXTRA_VAR_ARGS[@]+"${EXTRA_VAR_ARGS[@]}"}"
  -target=module.app_hosting
  -target=module.app_secrets
)

if [[ "${ACTION}" == "apply" ]]; then
  TF_ACTION_ARGS+=(-auto-approve)
fi

terraform -chdir="${TF_DIR}" "${ACTION}" "${TF_ACTION_ARGS[@]}"

if [[ "${ACTION}" != "apply" ]]; then
  exit 0
fi

# ── Wait for ECS service stability ───────────────────────────────────────────

if [[ "${NO_WAIT}" == "true" ]]; then
  echo "==> --no-wait set; skipping ECS stabilisation check."
  exit 0
fi

AWS_REGION="$(aws configure get region 2>/dev/null || echo "ap-southeast-2")"

CLUSTER_NAME="$(terraform -chdir="${TF_DIR}" output -raw ecs_cluster_name 2>/dev/null || true)"
SERVICE_NAME="$(terraform -chdir="${TF_DIR}" output -raw query_web_service_name 2>/dev/null || true)"

if [[ -z "${CLUSTER_NAME}" || -z "${SERVICE_NAME}" ]]; then
  echo "INFO: query-web service not enabled or Terraform outputs unavailable; skipping ECS wait."
  exit 0
fi

echo "==> Waiting for ECS service to stabilise"
echo "    Cluster: ${CLUSTER_NAME}"
echo "    Service: ${SERVICE_NAME}"

if aws ecs wait services-stable \
  --region "${AWS_REGION}" \
  --cluster "${CLUSTER_NAME}" \
  --services "${SERVICE_NAME}"; then
  echo "==> ECS service is stable."
else
  echo "WARNING: 'aws ecs wait services-stable' returned non-zero. Check the ECS console for task failure details." >&2
  echo "    aws ecs describe-services --cluster ${CLUSTER_NAME} --services ${SERVICE_NAME}"
fi

echo ""
echo "==> Rollout complete."
if [[ -n "${QUERY_WEB_TAG}" ]]; then
  echo "    query-web tag : ${QUERY_WEB_TAG}"
fi
if [[ -n "${INGESTION_TAG}" ]]; then
  echo "    ingestion tag : ${INGESTION_TAG}"
fi
