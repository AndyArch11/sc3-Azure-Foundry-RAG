#!/usr/bin/env bash
set -euo pipefail

# Executes Phase 2 scoped Terraform plan/apply for foundation, network, DNS, and bastion.

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

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required in PATH."
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Azure CLI is not authenticated. Run: az login"
  exit 1
fi

echo "==> Initialising Terraform root stack"
terraform -chdir="${TF_DIR}" init -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.foundation"
  "-target=module.network"
  "-target=module.dns"
  "-target=module.bastion_jumpbox"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 2 plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 2 apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 2 ${ACTION} completed for ${ENVIRONMENT}"
