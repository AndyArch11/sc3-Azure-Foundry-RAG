#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/azure/phase3-data-ai.sh <env> [plan|apply]

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

Optional environment variables:
  AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS=true
    Automatically approves pending Storage/Foundry private endpoint connections
    detected after apply.

Defaults:
  env    = dev
  action = plan
EOF
  exit 0
fi

# Executes Phase 3 scoped Terraform plan/apply for observability, data services,
# Foundry, private endpoints, identity, and bastion/jumpbox.
# Agent hosting (preview API) is deployed separately via phase3b-agent-hosting.sh.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/azure"

ENVIRONMENT="${1:-dev}"
ACTION="${2:-plan}"
AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS="${AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS:-false}"

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
  echo "==> Checking for pending private endpoint connection approvals (Storage/Foundry)"

  TF_OUTPUT_RG_NAME=$(terraform -chdir="${TF_DIR}" output -raw resource_group_name 2>/dev/null || true)
  TF_OUTPUT_STORAGE_ACCOUNT_NAME=$(terraform -chdir="${TF_DIR}" output -raw storage_account_name 2>/dev/null || true)
  TF_OUTPUT_AI_SERVICES_ENDPOINT=$(terraform -chdir="${TF_DIR}" output -raw ai_services_endpoint 2>/dev/null || true)

  FOUND_PENDING_APPROVALS=false
  STORAGE_PENDING_IDS=""
  FOUNDRY_PENDING_IDS=""

  if [[ -n "${TF_OUTPUT_RG_NAME}" && -n "${TF_OUTPUT_STORAGE_ACCOUNT_NAME}" ]]; then
    STORAGE_PENDING=$(az storage account private-endpoint-connection list \
      --account-name "${TF_OUTPUT_STORAGE_ACCOUNT_NAME}" \
      --resource-group "${TF_OUTPUT_RG_NAME}" \
      --query "[?to_string(properties.privateLinkServiceConnectionState.status)=='Pending'].{name:name,status:properties.privateLinkServiceConnectionState.status,description:properties.privateLinkServiceConnectionState.description}" \
      -o tsv 2>/dev/null || true)
    STORAGE_PENDING_IDS=$(az storage account private-endpoint-connection list \
      --account-name "${TF_OUTPUT_STORAGE_ACCOUNT_NAME}" \
      --resource-group "${TF_OUTPUT_RG_NAME}" \
      --query "[?to_string(properties.privateLinkServiceConnectionState.status)=='Pending'].id" \
      -o tsv 2>/dev/null || true)
    if [[ -n "${STORAGE_PENDING}" ]]; then
      FOUND_PENDING_APPROVALS=true
      echo "Pending Storage private endpoint connections detected:"
      echo "${STORAGE_PENDING}"
    fi
  fi

  if [[ -n "${TF_OUTPUT_RG_NAME}" && -n "${TF_OUTPUT_AI_SERVICES_ENDPOINT}" ]]; then
    FOUNDRY_ACCOUNT_NAME=$(echo "${TF_OUTPUT_AI_SERVICES_ENDPOINT}" | sed -E 's#https?://([^./]+).*#\1#')
    if [[ -n "${FOUNDRY_ACCOUNT_NAME}" ]]; then
      FOUNDRY_PENDING=$(az resource list \
        --resource-group "${TF_OUTPUT_RG_NAME}" \
        --resource-type "Microsoft.CognitiveServices/accounts/privateEndpointConnections" \
        --query "[?contains(id, '/accounts/${FOUNDRY_ACCOUNT_NAME}/') && to_string(properties.privateLinkServiceConnectionState.status)=='Pending'].{name:name,status:properties.privateLinkServiceConnectionState.status,description:properties.privateLinkServiceConnectionState.description}" \
        -o tsv 2>/dev/null || true)
      FOUNDRY_PENDING_IDS=$(az resource list \
        --resource-group "${TF_OUTPUT_RG_NAME}" \
        --resource-type "Microsoft.CognitiveServices/accounts/privateEndpointConnections" \
        --query "[?contains(id, '/accounts/${FOUNDRY_ACCOUNT_NAME}/') && to_string(properties.privateLinkServiceConnectionState.status)=='Pending'].id" \
        -o tsv 2>/dev/null || true)
      if [[ -n "${FOUNDRY_PENDING}" ]]; then
        FOUND_PENDING_APPROVALS=true
        echo "Pending Foundry private endpoint connections detected:"
        echo "${FOUNDRY_PENDING}"
      fi
    fi
  fi

  if [[ "${FOUND_PENDING_APPROVALS}" == "true" ]]; then
    echo ""
    if [[ "${AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS}" == "true" ]]; then
      echo "AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS=true, attempting to approve pending connections..."

      if [[ -n "${STORAGE_PENDING_IDS}" ]]; then
        while IFS= read -r pe_id; do
          [[ -z "${pe_id}" ]] && continue
          echo "Approving Storage private endpoint connection: ${pe_id}"
          az resource update \
            --ids "${pe_id}" \
            --set properties.privateLinkServiceConnectionState.status=Approved properties.privateLinkServiceConnectionState.description='Approved by phase3-data-ai.sh' \
            >/dev/null
        done <<< "${STORAGE_PENDING_IDS}"
      fi

      if [[ -n "${FOUNDRY_PENDING_IDS}" ]]; then
        while IFS= read -r pe_id; do
          [[ -z "${pe_id}" ]] && continue
          echo "Approving Foundry private endpoint connection: ${pe_id}"
          az resource update \
            --ids "${pe_id}" \
            --set properties.privateLinkServiceConnectionState.status=Approved properties.privateLinkServiceConnectionState.description='Approved by phase3-data-ai.sh' \
            >/dev/null
        done <<< "${FOUNDRY_PENDING_IDS}"
      fi

      echo "Auto-approval attempts completed."
    else
      echo "One or more private endpoint connections are pending approval."
      echo "Set AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS=true to auto-approve in this script."
      echo "Otherwise approve them manually before running Search indexer/skill paths that depend on private connectivity."
    fi
  else
    echo "No pending Storage/Foundry private endpoint connection approvals detected."
  fi
fi

if [[ "${ACTION}" == "apply" ]]; then
  echo "==> Re-applying bootstrap stack to grant agent runtime identity access to Terraform state storage"
  BOOTSTRAP_DIR="${ROOT_DIR}/infra/terraform/azure/bootstrap"
  AGENT_PRINCIPAL_ID=$(terraform -chdir="${TF_DIR}" output -raw agent_runtime_principal_id 2>/dev/null || true)
  if [[ -z "${AGENT_PRINCIPAL_ID}" ]]; then
    echo "Warning: Could not read agent_runtime_principal_id from Terraform outputs; skipping bootstrap re-apply."
  else
    DEPLOYING_PRINCIPAL_ID=$(az account show --query id -o tsv)
    LOCATION=$(grep '^location ' "${VAR_FILE}" | awk -F'"' '{print $2}')
    RESOURCE_GROUP_NAME=$(grep '^resource_group_name ' "${VAR_FILE}" | awk -F'"' '{print $2}' || echo "rg-tfstate-${ENVIRONMENT}")
    STATE_RG="rg-tfstate-${ENVIRONMENT}"
    STORAGE_ACCOUNT_PREFIX="sttfstate${ENVIRONMENT}"
    ENABLE_BOOTSTRAP_KEY_VAULT="${TF_ENABLE_BOOTSTRAP_KEY_VAULT:-true}"
    KEY_VAULT_PREFIX="${TF_KEY_VAULT_PREFIX:-kvtfstate}"
    KV_EXTRA_RBAC_JSON="${TF_KEY_VAULT_EXTRA_RBAC_OBJECT_IDS:-}"
    if [[ -n "${KV_EXTRA_RBAC_JSON}" ]]; then
      KV_EXTRA_RBAC_JSON="[\"$(echo "${KV_EXTRA_RBAC_JSON}" | sed 's/[[:space:]]*,[[:space:]]*/\",\"/g')\"]"
    else
      KV_EXTRA_RBAC_JSON="[]"
    fi
    DEPLOYING_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || az account show --query user.name -o tsv)
    STATE_READER_JSON="[\"${AGENT_PRINCIPAL_ID}\"]"
    STATE_BLOB_CONTRIBUTOR_JSON="[\"${AGENT_PRINCIPAL_ID}\",\"${DEPLOYING_OBJECT_ID}\"]"
    terraform -chdir="${BOOTSTRAP_DIR}" apply -auto-approve \
      -input=false \
      -parallelism=1 \
      -lock-timeout=5m \
      -var="location=${LOCATION}" \
      -var="resource_group_name=${STATE_RG}" \
      -var="storage_account_name_prefix=${STORAGE_ACCOUNT_PREFIX}" \
      -var="enable_bootstrap_key_vault=${ENABLE_BOOTSTRAP_KEY_VAULT}" \
      -var="key_vault_name_prefix=${KEY_VAULT_PREFIX}" \
      -var="key_vault_extra_rbac_principal_object_ids=${KV_EXTRA_RBAC_JSON}" \
      -var="state_storage_reader_principal_object_ids=${STATE_READER_JSON}" \
      -var="state_storage_blob_data_contributor_principal_object_ids=${STATE_BLOB_CONTRIBUTOR_JSON}"
    echo "==> Bootstrap re-apply complete; agent runtime identity now has state storage access"
  fi
fi
