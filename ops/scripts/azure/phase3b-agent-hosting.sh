#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ENABLE_HOSTED_QUERY_AGENT_PREVIEW=true ./ops/scripts/azure/phase3b-agent-hosting.sh <env> [plan|apply]

Runs the optional preview-only hosted query agent deployment path.

Defaults:
  env    = dev
  action = plan

Note:
  This path is disabled unless ENABLE_HOSTED_QUERY_AGENT_PREVIEW=true.

Optional environment variables:
  AUTO_APPROVE_PRIVATE_ENDPOINT_CONNECTIONS=true
    Automatically approves pending Storage/Foundry private endpoint connections
    detected after apply.
EOF
  exit 0
fi

# Deploys agent hosting module separately from Phase 3 core, because
# hosted_query_agent uses a preview API (2025-04-01-preview) that can
# be slow to respond. Resources here carry 30-minute timeouts.

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

if [[ "${ENABLE_HOSTED_QUERY_AGENT_PREVIEW:-false}" != "true" ]]; then
  echo "Hosted query agent deployment is disabled by default for network-isolated Foundry deployments."
  echo "This phase targets hosted-agent preview APIs that are not supported by standard private-network setup."
  echo "Set ENABLE_HOSTED_QUERY_AGENT_PREVIEW=true to force this path, or continue with standard-agent setup steps."
  exit 0
fi

echo "==> Initialising Terraform root stack"
terraform -chdir="${TF_DIR}" init -reconfigure -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.agent_hosting"
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
  echo "==> Running Phase 3b agent-hosting plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -var='enable_hosted_query_agent_preview=true' \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 3b agent-hosting apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -auto-approve \
    -var='enable_hosted_query_agent_preview=true' \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 3b ${ACTION} completed for ${ENVIRONMENT}"

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
            --set properties.privateLinkServiceConnectionState.status=Approved properties.privateLinkServiceConnectionState.description='Approved by phase3b-agent-hosting.sh' \
            >/dev/null
        done <<< "${STORAGE_PENDING_IDS}"
      fi

      if [[ -n "${FOUNDRY_PENDING_IDS}" ]]; then
        while IFS= read -r pe_id; do
          [[ -z "${pe_id}" ]] && continue
          echo "Approving Foundry private endpoint connection: ${pe_id}"
          az resource update \
            --ids "${pe_id}" \
            --set properties.privateLinkServiceConnectionState.status=Approved properties.privateLinkServiceConnectionState.description='Approved by phase3b-agent-hosting.sh' \
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
