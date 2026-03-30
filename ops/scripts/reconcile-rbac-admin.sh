#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/reconcile-rbac-admin.sh <env> [plan|apply]

Admin-only RBAC reconciliation for standard private-network deployments.
This script targets only role-assignment resources so jumpbox rollouts can stay
focused on Container App/Job resources without requiring RBAC write/delete rights.

Requirements:
  - Terraform and Azure CLI in PATH
  - Azure login with permissions to manage role assignments
    (Owner or User Access Administrator at required scopes)

Examples:
  ./ops/scripts/reconcile-rbac-admin.sh dev plan
  ./ops/scripts/reconcile-rbac-admin.sh dev apply
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
terraform -chdir="${TF_DIR}" init \
  -reconfigure \
  -backend-config="${BACKEND_FILE}" \
  -backend-config="use_azuread_auth=true"

EXTRA_VAR_FILE_ARGS=()
if [[ -f "${BOOTSTRAP_VARS_FILE}" ]]; then
  EXTRA_VAR_FILE_ARGS+=("-var-file=${BOOTSTRAP_VARS_FILE}")
fi

EXTRA_VAR_ARGS=(
  "-var=enable_hosted_query_agent_preview=false"
  # Keep root evaluation away from bootstrap Key Vault lookups in targeted runs.
  "-var=jumpbox_admin_ssh_public_key=ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIrbacadminplaceholderdonotuse rbac-admin"
)

TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

TARGET_ARGS=(
  "-target=module.identity.azurerm_role_assignment.storage_blob_contributor"
  "-target=module.identity.azurerm_role_assignment.search_index_contributor"
  "-target=module.identity.azurerm_role_assignment.search_service_contributor"
  "-target=module.identity.azurerm_role_assignment.cognitive_services_user"
  "-target=module.identity.azurerm_cosmosdb_sql_role_assignment.cosmos_data_contributor"
  "-target=module.identity.azurerm_role_assignment.foundry_project_manager"
  "-target=module.identity.azurerm_role_assignment.search_mi_storage_blob_reader"
  "-target=module.identity.azurerm_role_assignment.search_mi_openai_user"
  "-target=module.identity.azurerm_role_assignment.agent_runtime_network_reader"
  "-target=module.identity.azurerm_role_assignment.agent_runtime_rg_contributor"
  "-target=module.identity.azurerm_role_assignment.acr_pull"
  "-target=module.identity.azurerm_role_assignment.acr_push"
  "-target=module.identity.azurerm_role_assignment.log_analytics_reader"
  "-target=module.identity.azurerm_role_assignment.log_analytics_workspace_reader"
  "-target=module.identity.azurerm_role_assignment.log_analytics_contributor"
  "-target=module.identity.azurerm_role_assignment.terraform_state_reader"
  "-target=module.identity.azurerm_role_assignment.terraform_state_blob_data_contributor"
  "-target=module.identity.azurerm_role_assignment.cosmosdb_account_contributor"
  "-target=module.identity.azurerm_role_assignment.deployment_principal_terraform_state_reader"
  "-target=module.identity.azurerm_role_assignment.deployment_principal_terraform_state_blob_data_contributor"
  "-target=module.agent_hosting.azurerm_role_assignment.ingestion_job_contributor"
  "-target=module.agent_hosting.azurerm_role_assignment.query_web_contributor"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running admin RBAC plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    "${EXTRA_VAR_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running admin RBAC apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    "${EXTRA_VAR_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Admin RBAC ${ACTION} completed for ${ENVIRONMENT}"
