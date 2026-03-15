#!/usr/bin/env bash
set -euo pipefail

# Deploys agent hosting module separately from Phase 3 core, because
# hosted_query_agent uses a preview API (2025-04-01-preview) that can
# be slow to respond. Resources here carry 30-minute timeouts.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform"

ENVIRONMENT="${1:-dev}"
ACTION="${2:-plan}"

case "${ENVIRONMENT}" in
  dev|test|prod)
    ;;
  *)
    echo "Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod."
    exit 1
    ;;
esac

case "${ACTION}" in
  plan|apply)
    ;;
  *)
    echo "Unsupported action '${ACTION}'. Use one of: plan, apply."
    exit 1
    ;;
esac

BACKEND_FILE="${TF_DIR}/environments/${ENVIRONMENT}/backend.hcl"
VAR_FILE="${TF_DIR}/environments/${ENVIRONMENT}/${ENVIRONMENT}.tfvars"

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required in PATH."
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Azure CLI is not authenticated. Run: az login"
  exit 1
fi

echo "==> Initialising Terraform root stack"
terraform -chdir="${TF_DIR}" init -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.agent_hosting"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 3b agent-hosting plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 3b agent-hosting apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 3b ${ACTION} completed for ${ENVIRONMENT}"
