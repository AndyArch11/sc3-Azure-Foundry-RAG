#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/rollout-query-web-entra.sh <env> [plan|apply] [--include-redirect-uri] [--runtime-uami-principal-id <object-id>]

Runs the EXTERNAL/ADMIN Entra rollout path for query web auth resources only.

What this script does:
  - targets azuread_application.query_web
  - automatically targets azuread_application_redirect_uris.query_web when query web FQDN already exists
  - optionally forces redirect URI target with --include-redirect-uri
  - grants app ownership to the runtime managed identity
  - grants Microsoft Graph Application.ReadWrite.OwnedBy to the runtime managed identity
  - forces enable_hosted_query_agent_preview=false
  - bypasses bootstrap Key Vault lookup paths unrelated to Entra rollout

Examples:
  ./ops/scripts/rollout-query-web-entra.sh dev plan
  ./ops/scripts/rollout-query-web-entra.sh dev apply
  ./ops/scripts/rollout-query-web-entra.sh dev apply --include-redirect-uri
  ./ops/scripts/rollout-query-web-entra.sh dev apply --runtime-uami-principal-id "<uami-object-id>"
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform"

ENVIRONMENT="${1:-dev}"
ACTION="${2:-apply}"
shift $(( $# >= 1 ? 1 : 0 ))
shift $(( $# >= 1 ? 1 : 0 ))

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

INCLUDE_REDIRECT_URI="false"
RUNTIME_UAMI_PRINCIPAL_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-redirect-uri)
      INCLUDE_REDIRECT_URI="true"
      shift
      ;;
    --runtime-uami-principal-id)
      RUNTIME_UAMI_PRINCIPAL_ID="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Use --help for usage."
      exit 1
      ;;
  esac
done

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
  # Prevent unrelated bootstrap Key Vault lookups in root during targeted apply.
  "-var=jumpbox_admin_ssh_public_key=ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIstandardpathplaceholderdonotuse standard-path"
)

TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

TARGET_ARGS=(
  "-target=azuread_application.query_web"
)

QUERY_WEB_FQDN="$(terraform -chdir="${TF_DIR}" output -raw query_web_fqdn 2>/dev/null || true)"

if [[ "${INCLUDE_REDIRECT_URI}" == "true" || -n "${QUERY_WEB_FQDN}" ]]; then
  if [[ -n "${QUERY_WEB_FQDN}" ]]; then
    echo "==> Query web FQDN detected (${QUERY_WEB_FQDN}); including redirect URI target"
  else
    echo "==> Including redirect URI target"
  fi
  TARGET_ARGS+=("-target=azuread_application_redirect_uris.query_web")
fi

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running external Entra rollout plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    "${EXTRA_VAR_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running external Entra rollout apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    "${EXTRA_VAR_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"

  APP_CLIENT_ID="$(terraform -chdir="${TF_DIR}" output -raw query_web_entra_client_id 2>/dev/null || true)"
  RUNTIME_UAMI_OBJECT_ID="${RUNTIME_UAMI_PRINCIPAL_ID}"

  if [[ -z "${RUNTIME_UAMI_OBJECT_ID}" ]]; then
    RUNTIME_UAMI_OBJECT_ID="$(terraform -chdir="${TF_DIR}" output -raw agent_runtime_principal_id 2>/dev/null || true)"
  fi

  if [[ -z "${RUNTIME_UAMI_OBJECT_ID}" ]]; then
    RESOURCE_GROUP_NAME="$(terraform -chdir="${TF_DIR}" output -raw resource_group_name 2>/dev/null || true)"
    if [[ -n "${RESOURCE_GROUP_NAME}" ]]; then
      MATCHING_UAMI_COUNT="$(az identity list -g "${RESOURCE_GROUP_NAME}" \
        --query "[?starts_with(name,'id-agent-runtime-')] | length(@)" -o tsv 2>/dev/null || echo "0")"
      if [[ "${MATCHING_UAMI_COUNT}" == "1" ]]; then
        RUNTIME_UAMI_OBJECT_ID="$(az identity list -g "${RESOURCE_GROUP_NAME}" \
          --query "[?starts_with(name,'id-agent-runtime-')][0].principalId" -o tsv 2>/dev/null || true)"
      fi
    fi
  fi

  if [[ -z "${APP_CLIENT_ID}" || -z "${RUNTIME_UAMI_OBJECT_ID}" ]]; then
    echo "Unable to resolve app client ID and/or runtime UAMI principal ID for ownership assignment."
    echo "App: '${APP_CLIENT_ID}'  UAMI principal: '${RUNTIME_UAMI_OBJECT_ID}'"
    echo "Re-run with: --runtime-uami-principal-id <object-id>"
    exit 1
  fi

  OWNER_MATCH_COUNT="$(az ad app owner list --id "${APP_CLIENT_ID}" \
    --query "[?id=='${RUNTIME_UAMI_OBJECT_ID}'] | length(@)" -o tsv 2>/dev/null || echo "0")"

  if [[ "${OWNER_MATCH_COUNT}" == "0" ]]; then
    echo "==> Granting app ownership to runtime UAMI (${RUNTIME_UAMI_OBJECT_ID})"
    az ad app owner add --id "${APP_CLIENT_ID}" --owner-object-id "${RUNTIME_UAMI_OBJECT_ID}" >/dev/null
  else
    echo "==> Runtime UAMI already owns app ${APP_CLIENT_ID}; skipping owner assignment"
  fi

  GRAPH_SP_ID="$(az ad sp show --id 00000003-0000-0000-c000-000000000000 --query id -o tsv 2>/dev/null || true)"
  GRAPH_APP_ROLE_ID="$(az ad sp show --id 00000003-0000-0000-c000-000000000000 \
    --query "appRoles[?value=='Application.ReadWrite.OwnedBy' && contains(allowedMemberTypes, 'Application')].id | [0]" -o tsv 2>/dev/null || true)"

  if [[ -z "${GRAPH_SP_ID}" || -z "${GRAPH_APP_ROLE_ID}" ]]; then
    echo "Unable to resolve Microsoft Graph service principal or Application.ReadWrite.OwnedBy app role ID."
    exit 1
  fi

  GRAPH_ROLE_MATCH_COUNT="$(az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${RUNTIME_UAMI_OBJECT_ID}/appRoleAssignments" \
    --query "value[?resourceId=='${GRAPH_SP_ID}' && appRoleId=='${GRAPH_APP_ROLE_ID}'] | length(@)" \
    -o tsv 2>/dev/null || echo "0")"

  if [[ "${GRAPH_ROLE_MATCH_COUNT}" == "0" ]]; then
    echo "==> Granting Microsoft Graph Application.ReadWrite.OwnedBy to runtime UAMI (${RUNTIME_UAMI_OBJECT_ID})"
    az rest \
      --method POST \
      --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${RUNTIME_UAMI_OBJECT_ID}/appRoleAssignments" \
      --headers 'Content-Type=application/json' \
      --body "{\"principalId\":\"${RUNTIME_UAMI_OBJECT_ID}\",\"resourceId\":\"${GRAPH_SP_ID}\",\"appRoleId\":\"${GRAPH_APP_ROLE_ID}\"}" \
      >/dev/null
  else
    echo "==> Runtime UAMI already has Microsoft Graph Application.ReadWrite.OwnedBy; skipping app role assignment"
  fi
fi

echo "==> External Entra rollout ${ACTION} completed for ${ENVIRONMENT}"

echo "==> Entra app client ID output"
terraform -chdir="${TF_DIR}" output -raw query_web_entra_client_id || true
