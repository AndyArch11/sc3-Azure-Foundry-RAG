#!/usr/bin/env bash
set -euo pipefail

# Executes Phase 2 scoped Terraform plan/apply for foundation, network, DNS, and bastion.

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
ENABLE_BOOTSTRAP_KEY_VAULT="${TF_ENABLE_BOOTSTRAP_KEY_VAULT:-true}"

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
terraform -chdir="${TF_DIR}" init -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.foundation"
  "-target=module.network"
  "-target=module.dns"
  "-target=module.bastion_jumpbox"
)

# Safety-first defaults: serialize graph execution and wait for state lock.
TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 2 plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 2 apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"

  # Standalone demo convenience: store the jumpbox SSH public key in the
  # bootstrap Key Vault after jump host deployment. In production, key lifecycle
  # should be independently managed with stricter governance and networking.
  # Enterprise extension point: replace or remove this publish block when integrating
  # with enterprise secret lifecycle tooling.
  if [[ "${ENABLE_BOOTSTRAP_KEY_VAULT}" != "true" ]]; then
    echo "==> Skipping Key Vault publish: optional bootstrap Key Vault is disabled"
  else
    JUMPBOX_SSH_PUBLIC_KEY="$(sed -nE 's/^[[:space:]]*jumpbox_admin_ssh_public_key[[:space:]]*=[[:space:]]*"([^"]+)"[[:space:]]*$/\1/p' "${VAR_FILE}" | head -n 1)"
    if [[ -z "${JUMPBOX_SSH_PUBLIC_KEY}" || "${JUMPBOX_SSH_PUBLIC_KEY}" == "<set-me-ssh-public-key>" ]]; then
      echo "==> Skipping Key Vault publish: jumpbox_admin_ssh_public_key is not set in ${VAR_FILE}"
    else
      BOOTSTRAP_KV_NAME="$(terraform -chdir="${ROOT_DIR}/infra/terraform/bootstrap" output -raw key_vault_name 2>/dev/null || true)"
      if [[ -z "${BOOTSTRAP_KV_NAME}" ]]; then
        echo "==> Skipping Key Vault publish: bootstrap key_vault_name output not found (run phase1 bootstrap first)"
      else
        SECRET_NAME="jumpbox-admin-ssh-public-key-${ENVIRONMENT}"
        echo "==> Publishing jumpbox SSH public key to Key Vault secret ${SECRET_NAME}"
        az keyvault secret set \
          --vault-name "${BOOTSTRAP_KV_NAME}" \
          --name "${SECRET_NAME}" \
          --value "${JUMPBOX_SSH_PUBLIC_KEY}" \
          --only-show-errors >/dev/null
      fi
    fi
  fi
fi

echo "==> Phase 2 ${ACTION} completed for ${ENVIRONMENT}"
