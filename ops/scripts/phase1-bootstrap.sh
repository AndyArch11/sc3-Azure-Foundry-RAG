#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/phase1-bootstrap.sh <env>

Bootstraps remote Terraform state and writes:
  - infra/terraform/environments/<env>/backend.hcl
  - infra/terraform/environments/<env>/bootstrap.generated.tfvars

Supported environments:
  dev, test, prod
EOF
  exit 0
fi

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
GENERATED_BOOTSTRAP_VARS_FILE="${ROOT_DIR}/infra/terraform/environments/${ENVIRONMENT}/bootstrap.generated.tfvars"
ENABLE_BOOTSTRAP_KEY_VAULT="${TF_ENABLE_BOOTSTRAP_KEY_VAULT:-true}"
KEY_VAULT_PREFIX="${TF_KEY_VAULT_PREFIX:-kvtfstate}"
KEY_VAULT_EXTRA_RBAC_OBJECT_IDS="${TF_KEY_VAULT_EXTRA_RBAC_OBJECT_IDS:-}"

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required in PATH."
  exit 1
fi

# Safety-first defaults: serialise graph execution and wait for state lock.
TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

echo "==> Initialising bootstrap stack"
terraform -chdir="${BOOTSTRAP_DIR}" init -upgrade

echo "==> Applying bootstrap stack"
KV_EXTRA_RBAC_JSON="[]"
if [[ -n "${KEY_VAULT_EXTRA_RBAC_OBJECT_IDS}" ]]; then
  KV_EXTRA_RBAC_JSON="[\"$(echo "${KEY_VAULT_EXTRA_RBAC_OBJECT_IDS}" | sed 's/[[:space:]]*,[[:space:]]*/\",\"/g')\"]"
fi

terraform -chdir="${BOOTSTRAP_DIR}" apply -auto-approve \
  -input=false \
  "${TF_SAFETY_ARGS[@]}" \
  -var="location=${LOCATION}" \
  -var="resource_group_name=${RESOURCE_GROUP_NAME}" \
  -var="storage_account_name_prefix=${STORAGE_ACCOUNT_PREFIX}" \
  -var="enable_bootstrap_key_vault=${ENABLE_BOOTSTRAP_KEY_VAULT}" \
  -var="key_vault_name_prefix=${KEY_VAULT_PREFIX}" \
  -var="key_vault_extra_rbac_principal_object_ids=${KV_EXTRA_RBAC_JSON}"

echo "==> Reading bootstrap outputs"
STATE_RG="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw resource_group_name)"
STATE_SA="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw storage_account_name)"
STATE_CONTAINER="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw container_name)"
STATE_KEY_VAULT="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw key_vault_name 2>/dev/null || true)"

cat > "${BACKEND_FILE}" <<EOF
resource_group_name  = "${STATE_RG}"
storage_account_name = "${STATE_SA}"
container_name       = "${STATE_CONTAINER}"
key                  = "${BACKEND_KEY}"
EOF

cat > "${GENERATED_BOOTSTRAP_VARS_FILE}" <<EOF
bootstrap_state_storage_account_name = "${STATE_SA}"
bootstrap_state_storage_account_resource_group_name = "${STATE_RG}"
EOF

if [[ -n "${STATE_KEY_VAULT}" ]]; then
  cat >> "${GENERATED_BOOTSTRAP_VARS_FILE}" <<EOF
bootstrap_key_vault_name = "${STATE_KEY_VAULT}"
bootstrap_key_vault_resource_group_name = "${STATE_RG}"
jumpbox_ssh_public_key_secret_name = "jumpbox-admin-ssh-public-key-${ENVIRONMENT}"
EOF
fi

echo "==> Updated backend configuration: ${BACKEND_FILE}"
echo "   resource_group_name=${STATE_RG}"
echo "   storage_account_name=${STATE_SA}"
echo "   container_name=${STATE_CONTAINER}"
echo "   key=${BACKEND_KEY}"
if [[ -n "${STATE_KEY_VAULT}" ]]; then
  echo "   key_vault_name=${STATE_KEY_VAULT}"
  echo "   bootstrap_vars_file=${GENERATED_BOOTSTRAP_VARS_FILE}"
else
  echo "   key_vault_name=<disabled>"
  echo "   bootstrap_vars_file=${GENERATED_BOOTSTRAP_VARS_FILE}"
fi

echo "==> Phase 1 bootstrap completed successfully"
