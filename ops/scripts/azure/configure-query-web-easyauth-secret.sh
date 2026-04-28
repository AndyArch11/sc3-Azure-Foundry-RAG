#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/azure/configure-query-web-easyauth-secret.sh <env> --key-vault-name <kv-name> [--secret-name <name>] [--credential-display-name <name>] [--valid-days <days>]

Creates (append-only) an Entra app credential for the Terraform-managed query-web
app registration and writes the generated secret value to Azure Key Vault.

Defaults:
  secret-name            = query-web-entra-client-secret-<env>
  credential-display-name= easyauth-<env>
  valid-days             = 365

Examples:
  ./ops/scripts/azure/configure-query-web-easyauth-secret.sh dev --key-vault-name kv-app-secrets-dev
  ./ops/scripts/azure/configure-query-web-easyauth-secret.sh dev --key-vault-name kv-app-secrets-dev --secret-name query-web-easyauth-client-secret --valid-days 180
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/azure"

ENVIRONMENT="${1:-dev}"
shift $(( $# >= 1 ? 1 : 0 ))

case "${ENVIRONMENT}" in
  dev|test|prod)
    ;;
  *)
    echo "Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod."
    exit 1
    ;;
esac

KEY_VAULT_NAME=""
SECRET_NAME="query-web-entra-client-secret-${ENVIRONMENT}"
CREDENTIAL_DISPLAY_NAME="easyauth-${ENVIRONMENT}"
VALID_DAYS="365"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key-vault-name)
      KEY_VAULT_NAME="${2:-}"
      shift 2
      ;;
    --secret-name)
      SECRET_NAME="${2:-}"
      shift 2
      ;;
    --credential-display-name)
      CREDENTIAL_DISPLAY_NAME="${2:-}"
      shift 2
      ;;
    --valid-days)
      VALID_DAYS="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Use --help for usage."
      exit 1
      ;;
  esac
done

if ! [[ "${VALID_DAYS}" =~ ^[0-9]+$ ]]; then
  echo "--valid-days must be a positive integer."
  exit 1
fi

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

BACKEND_FILE="${TF_DIR}/environments/${ENVIRONMENT}/backend.hcl"

echo "==> Initialising Terraform root stack"
terraform -chdir="${TF_DIR}" init \
  -reconfigure \
  -backend-config="${BACKEND_FILE}" \
  -backend-config="use_azuread_auth=true" >/dev/null

APP_CLIENT_ID="$(terraform -chdir="${TF_DIR}" output -raw query_web_entra_client_id 2>/dev/null || true)"

if [[ -z "${KEY_VAULT_NAME}" ]]; then
  KEY_VAULT_NAME="$(terraform -chdir="${TF_DIR}" output -raw app_secrets_key_vault_name 2>/dev/null || true)"
fi

if [[ -z "${KEY_VAULT_NAME}" ]]; then
  echo "Unable to resolve Key Vault name from Terraform outputs."
  echo "Set it explicitly with --key-vault-name or run phase3c-app-secrets first."
  exit 1
fi

if [[ -z "${APP_CLIENT_ID}" ]]; then
  echo "Unable to read query_web_entra_client_id from Terraform outputs."
  echo "Run external Entra bootstrap first to create the app registration target, then rerun this script:"
  echo "  ./ops/scripts/azure/rollout-query-web-entra.sh ${ENVIRONMENT} apply"
  exit 1
fi

echo "==> Validating private Key Vault connectivity (${KEY_VAULT_NAME}.vault.azure.net)"
KV_RESOLUTION="$(getent ahostsv4 "${KEY_VAULT_NAME}.vault.azure.net" 2>/dev/null | awk '{print $1}' | sort -u | xargs || true)"
if [[ -n "${KV_RESOLUTION}" ]]; then
  echo "Resolved ${KEY_VAULT_NAME}.vault.azure.net to: ${KV_RESOLUTION}"
else
  echo "Failed to resolve ${KEY_VAULT_NAME}.vault.azure.net from this host."
fi

set +e
az keyvault secret list \
  --vault-name "${KEY_VAULT_NAME}" \
  --maxresults 1 \
  --query "[].id" -o tsv >/tmp/query-web-easyauth-kv-preflight.out 2>/tmp/query-web-easyauth-kv-preflight.err
KV_PREFLIGHT_RC=$?
set -e

if [[ ${KV_PREFLIGHT_RC} -ne 0 ]]; then
  echo "Unable to reach Key Vault data plane through an approved private path before creating app credentials."
  cat /tmp/query-web-easyauth-kv-preflight.err
  echo ""
  echo "Remediation:"
  echo "  1. Re-run private DNS provisioning/link:"
  echo "     ./ops/scripts/azure/phase2-network-dns.sh ${ENVIRONMENT} apply"
  echo "  2. Re-run app-secrets private endpoint deployment:"
  echo "     ./ops/scripts/azure/phase3c-app-secrets.sh ${ENVIRONMENT} apply"
  echo "  3. From jumpbox, verify private resolution:" 
  echo "     getent ahostsv4 ${KEY_VAULT_NAME}.vault.azure.net"
  echo ""
  echo "Credential creation skipped so no orphan Entra app secret was added."
  exit 1
fi

END_DATE="$(date -u -d "+${VALID_DAYS} days" +"%Y-%m-%dT%H:%M:%SZ")"

echo "==> Creating Entra app credential for app ${APP_CLIENT_ID}"
set +e
NEW_SECRET_VALUE="$(az ad app credential reset \
  --id "${APP_CLIENT_ID}" \
  --append \
  --display-name "${CREDENTIAL_DISPLAY_NAME}" \
  --end-date "${END_DATE}" \
  --query password -o tsv 2>/tmp/query-web-easyauth-credential.err)"
AZ_CRED_RC=$?
set -e

if [[ ${AZ_CRED_RC} -ne 0 ]]; then
  echo "Failed to create Entra app credential for app ${APP_CLIENT_ID}."
  cat /tmp/query-web-easyauth-credential.err
  echo ""
  echo "This jumpbox identity needs Entra permissions to manage app credentials."
  echo "For managed identity / app-only Azure CLI auth, grant one of the following:"
  echo "  - App ownership on this app registration AND Microsoft Graph Application.ReadWrite.OwnedBy"
  echo "  - Directory role with app credential write rights (for example Application Administrator)"
  echo ""
  echo "Fallback: run app credential creation from external admin context, then set Key Vault secret from jumpbox."
  exit 1
fi

if [[ -z "${NEW_SECRET_VALUE}" ]]; then
  echo "Failed to create Entra app credential."
  exit 1
fi

echo "==> Writing client secret value to Key Vault secret ${KEY_VAULT_NAME}/${SECRET_NAME}"
set +e
SECRET_ID="$(az keyvault secret set \
  --vault-name "${KEY_VAULT_NAME}" \
  --name "${SECRET_NAME}" \
  --value "${NEW_SECRET_VALUE}" \
  --content-type "query-web-easyauth-client-secret" \
  --query id -o tsv 2>/tmp/query-web-easyauth-kv.err)"
AZ_KV_RC=$?
set -e

if [[ ${AZ_KV_RC} -ne 0 ]]; then
  echo "Failed to write secret to Key Vault ${KEY_VAULT_NAME}."
  cat /tmp/query-web-easyauth-kv.err
  echo ""
  echo "This is usually a private endpoint / DNS path issue rather than Key Vault RBAC."
  echo "The jumpbox request is reaching the public Key Vault endpoint instead of the private endpoint."
  echo ""
  echo "Recommended checks:"
  echo "  1. Re-run phase 2 to ensure privatelink.vaultcore.azure.net DNS zone exists and is linked:"
  echo "     ./ops/scripts/azure/phase2-network-dns.sh ${ENVIRONMENT} apply"
  echo "  2. Re-run phase 3c to ensure the Key Vault private endpoint and zone group are present:"
  echo "     ./ops/scripts/azure/phase3c-app-secrets.sh ${ENVIRONMENT} apply"
  echo "  3. From jumpbox, verify ${KEY_VAULT_NAME}.vault.azure.net resolves to a private IP:"
  echo "     getent ahostsv4 ${KEY_VAULT_NAME}.vault.azure.net"
  echo ""
  echo "If your VNet uses custom DNS, ensure it forwards privatelink.vaultcore.azure.net correctly."
  exit 1
fi

if [[ -z "${SECRET_ID}" ]]; then
  echo "Failed to write secret to Key Vault."
  exit 1
fi

echo "==> Done"
echo "App client ID: ${APP_CLIENT_ID}"
echo "Secret ID: ${SECRET_ID}"
echo "Next rollout command:"
echo "  sudo ./ops/scripts/azure/rollout-agent-hosting.sh ${ENVIRONMENT} apply --entra-secret-kv ${KEY_VAULT_NAME} --entra-secret-name ${SECRET_NAME}"
