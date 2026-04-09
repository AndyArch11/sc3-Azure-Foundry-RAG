#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/rollout-agent-hosting.sh <env> [plan|apply] [--ingestion-tag <tag>] [--query-web-tag <tag>] [--confluence-poller-tag <tag>] [--enable-confluence-poller] [--disable-confluence-poller] [--entra-secret-kv <kv-name>] [--entra-secret-name <secret-name>] [--confluence-base-url <url>] [--confluence-auth-mode <basic|bearer|oauth>] [--confluence-auth-email <email>] [--confluence-api-token <token>] [--confluence-cloud-id <cloud-id>] [--confluence-account-id <account-id>] [--confluence-space-keys <KEY1,KEY2,...>] [--repair-query-web-reply-url]

Runs the STANDARD (non-preview) rollout for module.agent_hosting only.

What this script does:
  - targets module.agent_hosting only
  - skips state refresh (avoids Entra app-registration read permissions on jumpbox identities)
  - forces enable_hosted_query_agent_preview=false
  - bypasses bootstrap Key Vault lookup paths that are unrelated to agent_hosting
  - deploys non-RBAC app resources only (role assignments are reconciled separately)
  - optionally overrides image tags for ingestion/query-web/confluence-poller
  - optionally enables or disables the confluence poller app
  - can resolve Entra EasyAuth secret ID from a private Key Vault
  - Confluence config flags (--confluence-*) override dev.tfvars defaults at apply time;
    use --confluence-api-token to pass the secret without writing it to tfvars
  - validates query-web Entra reply URL post-apply to catch AADSTS500113 drift early
  - optional --repair-query-web-reply-url attempts az ad app update when reply URL is missing

Examples:
  ./ops/scripts/rollout-agent-hosting.sh dev apply
  ./ops/scripts/rollout-agent-hosting.sh dev apply --ingestion-tag 202603292354-8115700 --query-web-tag 202603292347-8115700
  ./ops/scripts/rollout-agent-hosting.sh dev apply --confluence-poller-tag 202604041530-a1b2c3d --enable-confluence-poller
  ./ops/scripts/rollout-agent-hosting.sh dev apply --query-web-tag 202603292347-8115700 --entra-secret-kv kv-app-secrets-dev
  ./ops/scripts/rollout-agent-hosting.sh dev apply \
    --enable-confluence-poller \
    --confluence-base-url https://myorg.atlassian.net \
    --confluence-auth-mode basic \
    --confluence-auth-email svc@myorg.com \
    --confluence-api-token '<token>' \
    --confluence-space-keys 'SEC,GRC'

  # Example for Atlassian scoped token path (Bearer + cloud-id)
  ./ops/scripts/rollout-agent-hosting.sh dev apply \
    --enable-confluence-poller \
    --confluence-base-url https://myorg.atlassian.net \
    --confluence-auth-mode bearer \
    --confluence-cloud-id '<cloud-id>' \
    --confluence-api-token '<token>'
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
CONFLUENCE_BASE_URL=""
CONFLUENCE_AUTH_MODE=""
CONFLUENCE_AUTH_EMAIL=""
CONFLUENCE_API_TOKEN=""
CONFLUENCE_CLOUD_ID=""
CONFLUENCE_ACCOUNT_ID=""
CONFLUENCE_SPACE_KEYS=""
REPAIR_QUERY_WEB_REPLY_URL="false"

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
    --confluence-base-url)
      CONFLUENCE_BASE_URL="${2:-}"
      shift 2
      ;;
    --confluence-auth-mode)
      CONFLUENCE_AUTH_MODE="${2:-}"
      shift 2
      ;;
    --confluence-auth-email)
      CONFLUENCE_AUTH_EMAIL="${2:-}"
      shift 2
      ;;
    --confluence-api-token)
      CONFLUENCE_API_TOKEN="${2:-}"
      shift 2
      ;;
    --confluence-cloud-id)
      CONFLUENCE_CLOUD_ID="${2:-}"
      shift 2
      ;;
    --confluence-account-id)
      CONFLUENCE_ACCOUNT_ID="${2:-}"
      shift 2
      ;;
    --confluence-space-keys)
      CONFLUENCE_SPACE_KEYS="${2:-}"
      shift 2
      ;;
    --repair-query-web-reply-url)
      REPAIR_QUERY_WEB_REPLY_URL="true"
      shift 1
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

# Confluence overrides are placed after the var-file so they win over dev.tfvars defaults.
LATE_OVERRIDE_ARGS=()
if [[ -n "${CONFLUENCE_BASE_URL}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_base_url=${CONFLUENCE_BASE_URL}")
fi
if [[ -n "${CONFLUENCE_AUTH_MODE}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_auth_mode=${CONFLUENCE_AUTH_MODE}")
fi
if [[ -n "${CONFLUENCE_AUTH_EMAIL}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_auth_email=${CONFLUENCE_AUTH_EMAIL}")
fi
if [[ -n "${CONFLUENCE_API_TOKEN}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_api_token=${CONFLUENCE_API_TOKEN}")
fi
if [[ -n "${CONFLUENCE_CLOUD_ID}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_cloud_id=${CONFLUENCE_CLOUD_ID}")
fi
if [[ -n "${CONFLUENCE_ACCOUNT_ID}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_account_id=${CONFLUENCE_ACCOUNT_ID}")
fi
if [[ -n "${CONFLUENCE_SPACE_KEYS}" ]]; then
  # Convert "SEC,GRC" -> ["SEC","GRC"] for Terraform HCL list syntax.
  _hcl_keys=""
  IFS=',' read -ra _key_arr <<< "${CONFLUENCE_SPACE_KEYS}"
  for _k in "${_key_arr[@]}"; do
    _k="$(echo "${_k}" | xargs)"  # trim surrounding whitespace
    _hcl_keys+="\"${_k}\","
  done
  LATE_OVERRIDE_ARGS+=("-var=confluence_poll_space_keys=[${_hcl_keys%,}]")
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
    "${LATE_OVERRIDE_ARGS[@]}" \
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
    "${LATE_OVERRIDE_ARGS[@]}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Standard agent_hosting ${ACTION} completed for ${ENVIRONMENT}"

if [[ "${ACTION}" == "apply" ]]; then
  # Validate reply URL wiring to prevent AADSTS500113 (No reply address is registered).
  QUERY_WEB_CLIENT_ID="$(terraform -chdir="${TF_DIR}" output -raw query_web_entra_client_id 2>/dev/null || true)"
  QUERY_WEB_FQDN="$(terraform -chdir="${TF_DIR}" output -raw query_web_fqdn 2>/dev/null || true)"

  if [[ -n "${QUERY_WEB_CLIENT_ID}" && -n "${QUERY_WEB_FQDN}" ]]; then
    EXPECTED_REPLY_URL="https://${QUERY_WEB_FQDN}/.auth/login/aad/callback"
    CURRENT_REPLY_URIS="$(az ad app show --id "${QUERY_WEB_CLIENT_ID}" --query "web.redirectUris" -o tsv 2>/dev/null || true)"

    if [[ -z "${CURRENT_REPLY_URIS}" || "${CURRENT_REPLY_URIS}" != *"${EXPECTED_REPLY_URL}"* ]]; then
      echo "WARNING: Query-web Entra reply URL is missing or out of sync."
      echo "Expected: ${EXPECTED_REPLY_URL}"
      echo "App ID:   ${QUERY_WEB_CLIENT_ID}"
      if [[ "${REPAIR_QUERY_WEB_REPLY_URL}" == "true" ]]; then
        echo "==> Attempting repair via az ad app update"
        if az ad app update --id "${QUERY_WEB_CLIENT_ID}" --web-redirect-uris "${EXPECTED_REPLY_URL}" >/dev/null 2>&1; then
          echo "==> Repaired query-web Entra reply URL"
        else
          echo "WARNING: Automatic repair failed (likely insufficient Entra app permissions)."
          echo "Run with an admin identity:"
          echo "  az ad app update --id ${QUERY_WEB_CLIENT_ID} --web-redirect-uris ${EXPECTED_REPLY_URL}"
        fi
      else
        echo "To repair now (admin context):"
        echo "  az ad app update --id ${QUERY_WEB_CLIENT_ID} --web-redirect-uris ${EXPECTED_REPLY_URL}"
      fi
    else
      echo "==> Verified query-web Entra reply URL is configured"
    fi
  fi
fi
