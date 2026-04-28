#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/phase3c-app-secrets.sh <env> [plan|apply]

Runs a focused Terraform plan/apply for the private app-secrets Key Vault path.

What this deploys:
  - module.identity (runtime managed identity role wiring)
  - module.app_secrets (private Key Vault + private endpoint + DNS link)

Defaults:
  env    = dev
  action = plan
EOF
  exit 0
fi

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
BOOTSTRAP_VARS_FILE="${TF_DIR}/environments/${ENVIRONMENT}/bootstrap.generated.tfvars"

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
terraform -chdir="${TF_DIR}" init -reconfigure -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.identity"
  "-target=module.app_secrets"
)

EXTRA_VAR_FILE_ARGS=()
if [[ -f "${BOOTSTRAP_VARS_FILE}" ]]; then
  EXTRA_VAR_FILE_ARGS+=("-var-file=${BOOTSTRAP_VARS_FILE}")
fi

TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 3c app-secrets plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 3c app-secrets apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 3c ${ACTION} completed for ${ENVIRONMENT}"
