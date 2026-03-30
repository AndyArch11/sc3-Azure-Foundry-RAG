#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/rollout-query-web-entra.sh <env> [plan|apply] [--include-redirect-uri]

Runs the EXTERNAL/ADMIN Entra rollout path for query web auth resources only.

What this script does:
  - targets azuread_application.query_web
  - optionally targets azuread_application_redirect_uris.query_web
  - forces enable_hosted_query_agent_preview=false
  - bypasses bootstrap Key Vault lookup paths unrelated to Entra rollout

Examples:
  ./ops/scripts/rollout-query-web-entra.sh dev plan
  ./ops/scripts/rollout-query-web-entra.sh dev apply
  ./ops/scripts/rollout-query-web-entra.sh dev apply --include-redirect-uri
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-redirect-uri)
      INCLUDE_REDIRECT_URI="true"
      shift
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

if [[ "${INCLUDE_REDIRECT_URI}" == "true" ]]; then
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
fi

echo "==> External Entra rollout ${ACTION} completed for ${ENVIRONMENT}"

echo "==> Entra app client ID output"
terraform -chdir="${TF_DIR}" output -raw query_web_entra_client_id || true
