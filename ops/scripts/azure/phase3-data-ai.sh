#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/phase3-data-ai.sh <env> [plan|apply]

Runs Phase 3 Terraform targets for observability, data services, Foundry,
private endpoints, identity, and bastion/jumpbox.

The bastion and jumpbox live here (not phase 2) because they depend on the
identity module which provides the runtime managed identity.

USAGE SCENARIOS:

1. Standard path (phase 2 creates network):
   Run phase2-network-dns.sh first, then phase3-data-ai.sh.

2. BYOL path (bring-your-own-network):
   Skip phase 2 entirely. Set byol_* variables for network resource IDs in tfvars or as -var options,
   then run only phase3-data-ai.sh to deploy Foundry components into your pre-existing network.
   Note: bastion/jumpbox are NOT created in BYOL mode.

Agent hosting is deployed separately via phase3b-agent-hosting.sh.

Defaults:
  env    = dev
  action = plan
EOF
  exit 0
fi

# Executes Phase 3 scoped Terraform plan/apply for observability, data services,
# Foundry, private endpoints, identity, and bastion/jumpbox.
# Agent hosting (preview API) is deployed separately via phase3b-agent-hosting.sh.

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

echo "==> Recovering soft-deleted Key Vaults (if any)"
# Recover any soft-deleted KV that matches our pattern to avoid conflicts
RESOURCE_GROUP_NAME=$(grep 'resource_group_name' "${VAR_FILE}" | awk -F'"' '{print $2}')
LOCATION=$(grep '^location ' "${VAR_FILE}" | awk -F'"' '{print $2}')
LOCATION_SHORT=$(grep 'location_short' "${VAR_FILE}" | awk -F'"' '{print $2}')
INSTANCE=$(grep '^instance ' "${VAR_FILE}" | awk -F'"' '{print $2}' || echo "001")

# Construct expected KV name pattern: kvapp{env}-{location_short}-{instance} with hyphens removed
EXPECTED_KV_PATTERN="kvapp"

# Find and purge any soft-deleted KVs in the same RG
echo "Checking for soft-deleted Key Vaults in ${RESOURCE_GROUP_NAME}..."
for vault in $(az keyvault list-deleted --query "[?properties.location=='${LOCATION}'].name" -o tsv 2>/dev/null || true); do
  if [[ "$vault" == kvapp* ]]; then
    echo "Purging soft-deleted Key Vault: ${vault}"
    az keyvault purge --name "${vault}" --location "${LOCATION}" || echo "Failed to purge ${vault}; may retry on next attempt"
  fi
done

echo "==> Removing any management locks on state storage account"
# Remove locks on the Terraform state storage account to allow role assignment operations
STATE_RG="rg-tfstate-${ENVIRONMENT}"
# Find and remove any locks on state storage accounts
for sa_name in $(az storage account list --resource-group "${STATE_RG}" --query "[?starts_with(name, 'sttfstate')].name" -o tsv 2>/dev/null || true); do
  SA_ID="/subscriptions/$(az account show --query id -o tsv)/resourceGroups/${STATE_RG}/providers/Microsoft.Storage/storageAccounts/${sa_name}"
  for lock_id in $(az lock list --resource-group "${STATE_RG}" --query "[?scope=='${SA_ID}'].id" -o tsv 2>/dev/null || true); do
    echo "Removing lock: ${lock_id}"
    az lock delete --ids "${lock_id}" 2>/dev/null || echo "Lock already removed"
  done
done

echo "==> Initialising Terraform root stack"
terraform -chdir="${TF_DIR}" init -reconfigure -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.observability"
  "-target=module.data_services"
  "-target=module.foundry"
  "-target=module.private_endpoints"
  "-target=module.identity"
  "-target=module.app_secrets"
  "-target=module.bastion_jumpbox"
)

EXTRA_VAR_FILE_ARGS=()
if [[ -f "${BOOTSTRAP_VARS_FILE}" ]]; then
  EXTRA_VAR_FILE_ARGS+=("-var-file=${BOOTSTRAP_VARS_FILE}")
fi

# Safety-first defaults: serialise graph execution and wait for state lock.
TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 3 plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 3 apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 3 ${ACTION} completed for ${ENVIRONMENT}"

if [[ "${ACTION}" == "apply" ]]; then
  echo "==> Granting agent runtime identity access to Terraform state storage"
  STATE_RG="rg-tfstate-${ENVIRONMENT}"
  LOCATION=$(grep '^location ' "${VAR_FILE}" | awk -F'"' '{print $2}')
  LOCATION_SHORT=$(grep 'location_short' "${VAR_FILE}" | awk -F'"' '{print $2}')
  INSTANCE=$(grep '^instance ' "${VAR_FILE}" | awk -F'"' '{print $2}' || echo "001")
  IDENTITY_NAME="id-agent-runtime-${ENVIRONMENT}-${LOCATION_SHORT}-${INSTANCE}"
  STATE_SA_NAME=$(az storage account list --resource-group "${STATE_RG}" --query "[?starts_with(name, 'sttfstate')].name" -o tsv 2>/dev/null | head -1 || true)
  
  if [[ -n "${STATE_SA_NAME}" ]]; then
    PRINCIPAL_ID=$(az identity list --query "[?name=='${IDENTITY_NAME}'].principalId" -o tsv 2>/dev/null || true)
    if [[ -n "${PRINCIPAL_ID}" ]]; then
      SA_ID="/subscriptions/$(az account show --query id -o tsv)/resourceGroups/${STATE_RG}/providers/Microsoft.Storage/storageAccounts/${STATE_SA_NAME}"
      echo "Assigning Storage Blob Data Contributor role to ${IDENTITY_NAME} on ${STATE_SA_NAME}"
      az role assignment create --role "Storage Blob Data Contributor" --assignee-object-id "${PRINCIPAL_ID}" --scope "${SA_ID}" 2>/dev/null || echo "Role assignment already exists"
    else
      echo "Warning: Could not find agent runtime identity ${IDENTITY_NAME}"
    fi
  else
    echo "Warning: Could not find Terraform state storage account in ${STATE_RG}"
  fi
fi
