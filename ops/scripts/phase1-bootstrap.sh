#!/usr/bin/env bash
set -euo pipefail

# Runs bootstrap Terraform and updates backend.hcl for the selected environment.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP_DIR="${ROOT_DIR}/infra/terraform/bootstrap"

ENVIRONMENT="${1:-dev}"
LOCATION="${TF_LOCATION:-australiaeast}"

case "${ENVIRONMENT}" in
  dev|test|prod)
    ;;
  *)
    echo "Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod."
    exit 1
    ;;
esac

RESOURCE_GROUP_NAME="${TF_STATE_RESOURCE_GROUP:-rg-tfstate-${ENVIRONMENT}}"
STORAGE_ACCOUNT_PREFIX="${TF_STATE_STORAGE_PREFIX:-sttfstate${ENVIRONMENT}}"
BACKEND_KEY="${TF_BACKEND_KEY:-platform/${ENVIRONMENT}.tfstate}"
BACKEND_FILE="${ROOT_DIR}/infra/terraform/environments/${ENVIRONMENT}/backend.hcl"

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required in PATH."
  exit 1
fi

echo "==> Initialising bootstrap stack"
terraform -chdir="${BOOTSTRAP_DIR}" init -upgrade

echo "==> Applying bootstrap stack"
terraform -chdir="${BOOTSTRAP_DIR}" apply -auto-approve \
  -input=false \
  -var="location=${LOCATION}" \
  -var="resource_group_name=${RESOURCE_GROUP_NAME}" \
  -var="storage_account_name_prefix=${STORAGE_ACCOUNT_PREFIX}"

echo "==> Reading bootstrap outputs"
STATE_RG="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw resource_group_name)"
STATE_SA="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw storage_account_name)"
STATE_CONTAINER="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw container_name)"

cat > "${BACKEND_FILE}" <<EOF
resource_group_name  = "${STATE_RG}"
storage_account_name = "${STATE_SA}"
container_name       = "${STATE_CONTAINER}"
key                  = "${BACKEND_KEY}"
EOF

echo "==> Updated backend configuration: ${BACKEND_FILE}"
echo "   resource_group_name=${STATE_RG}"
echo "   storage_account_name=${STATE_SA}"
echo "   container_name=${STATE_CONTAINER}"
echo "   key=${BACKEND_KEY}"

echo "==> Phase 1 bootstrap completed successfully"
