#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/rollout-agent-hosting.sh <env> [plan|apply] [--ingestion-tag <tag>] [--query-web-tag <tag>] [--confluence-poller-tag <tag>] [--enable-confluence-poller] [--disable-confluence-poller] [--entra-secret-kv <kv-name>] [--entra-secret-name <secret-name>]

Runs the STANDARD (non-preview) rollout for module.agent_hosting only.

What this script does:
  - targets module.agent_hosting only
  - skips state refresh (avoids Entra app-registration read permissions on jumpbox identities)
  - forces enable_hosted_query_agent_preview=false
  - bypasses bootstrap Key Vault lookup paths that are unrelated to agent_hosting
  - optionally overrides image tags for ingestion/query-web/confluence-poller
  - optionally enables or disables the confluence poller app
  - can resolve Entra EasyAuth secret ID from a private Key Vault

Examples:
  ./ops/scripts/rollout-agent-hosting.sh dev apply
  ./ops/scripts/rollout-agent-hosting.sh dev apply --ingestion-tag 202603292354-8115700 --query-web-tag 202603292347-8115700
  ./ops/scripts/rollout-agent-hosting.sh dev apply --confluence-poller-tag 202604041530-a1b2c3d --enable-confluence-poller
  ./ops/scripts/rollout-agent-hosting.sh dev apply --query-web-tag 202603292347-8115700 --entra-secret-kv kv-app-secrets-dev
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

INGESTION_TAG=""
QUERY_WEB_TAG=""
CONFLUENCE_POLLER_TAG=""
ENABLE_CONFLUENCE_POLLER=""
ENTRA_SECRET_KV=""
ENTRA_SECRET_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ingestion-tag)
      INGESTION_TAG="${2:-}"
      shift 2
      ;;
    --query-web-tag)
      QUERY_WEB_TAG="${2:-}"
      shift 2
      ;;
    --confluence-poller-tag)
      CONFLUENCE_POLLER_TAG="${2:-}"
      shift 2
      ;;
    --enable-confluence-poller)
      ENABLE_CONFLUENCE_POLLER="true"
      shift 1
      ;;
    --disable-confluence-poller)
      ENABLE_CONFLUENCE_POLLER="false"
      shift 1
      ;;
    --entra-secret-kv)
      ENTRA_SECRET_KV="${2:-}"
      shift 2
      ;;
    --entra-secret-name)
      ENTRA_SECRET_NAME="${2:-}"
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

if [[ -n "${INGESTION_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=ingestion_job_image_tag=${INGESTION_TAG}")
fi

if [[ -n "${QUERY_WEB_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=query_web_image_tag=${QUERY_WEB_TAG}")
fi

if [[ -n "${CONFLUENCE_POLLER_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=confluence_poller_image_tag=${CONFLUENCE_POLLER_TAG}")
fi

if [[ -n "${ENABLE_CONFLUENCE_POLLER}" ]]; then
  EXTRA_VAR_ARGS+=("-var=enable_confluence_poller_app=${ENABLE_CONFLUENCE_POLLER}")
fi

if [[ -n "${ENTRA_SECRET_KV}" ]]; then
  if [[ -z "${ENTRA_SECRET_NAME}" ]]; then
    ENTRA_SECRET_NAME="query-web-entra-client-secret-${ENVIRONMENT}"
  fi

  echo "==> Resolving Entra EasyAuth secret ID from Key Vault (${ENTRA_SECRET_KV}/${ENTRA_SECRET_NAME})"
  ENTRA_SECRET_ID="$(az keyvault secret show \
    --vault-name "${ENTRA_SECRET_KV}" \
    --name "${ENTRA_SECRET_NAME}" \
    --query id -o tsv)"

  if [[ -z "${ENTRA_SECRET_ID}" ]]; then
    echo "Failed to resolve Key Vault secret ID for ${ENTRA_SECRET_KV}/${ENTRA_SECRET_NAME}."
    exit 1
  fi

  EXTRA_VAR_ARGS+=("-var=query_web_entra_client_secret_key_vault_secret_id=${ENTRA_SECRET_ID}")
fi

TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

TARGET_ARGS=(
  "-target=module.agent_hosting.azurerm_container_app_environment.this"
  "-target=module.agent_hosting.azurerm_private_dns_zone.container_apps"
  "-target=module.agent_hosting.azurerm_private_dns_zone_virtual_network_link.container_apps"
  "-target=module.agent_hosting.azurerm_private_dns_a_record.ingestion_job"
  "-target=module.agent_hosting.azurerm_private_dns_a_record.query_web"
  "-target=module.agent_hosting.azurerm_private_dns_a_record.query_web_vnet"
  "-target=module.agent_hosting.azurerm_container_app_job.ingestion"
  "-target=module.agent_hosting.azurerm_container_app.query_web"
  "-target=module.agent_hosting.azurerm_container_app.confluence_poller"
  "-target=module.agent_hosting.azapi_resource.query_web_auth"
  "-target=module.agent_hosting.azurerm_role_assignment.confluence_poller_contributor"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running standard agent_hosting plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    -refresh=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    "${EXTRA_VAR_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running standard agent_hosting apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    -refresh=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    "${EXTRA_VAR_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Standard agent_hosting ${ACTION} completed for ${ENVIRONMENT}"
