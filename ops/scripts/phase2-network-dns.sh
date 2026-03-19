#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/phase2-network-dns.sh <env> [plan|apply]

Runs Phase 2 Terraform targets for foundation, network, DNS, and bastion/jumpbox.

Defaults:
  env    = dev
  action = plan
EOF
  exit 0
fi

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
BOOTSTRAP_VARS_FILE="${TF_DIR}/environments/${ENVIRONMENT}/bootstrap.generated.tfvars"
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

EXTRA_VAR_FILE_ARGS=()
if [[ -f "${BOOTSTRAP_VARS_FILE}" ]]; then
  EXTRA_VAR_FILE_ARGS+=("-var-file=${BOOTSTRAP_VARS_FILE}")
fi

# Safety-first defaults: serialise graph execution and wait for state lock.
TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

# --- Sandbox SSH keypair seeding function ------------------------------------
# Idempotent: checks Key Vault first, then uses a tfvars key if real, otherwise
# auto-generates a fresh ed25519 pair and stores BOTH the public key (used by
# Terraform to configure the VM) and the private key (retrieved by operators to
# SSH into the jumpbox via Bastion).
#
# Enterprise extension point: remove this function and manage key lifecycle
# independently (e.g. per-operator keys injected via az vm user update).
_seed_jumpbox_ssh_key() {
  local kv_name="$1" env="$2" var_file="$3"
  local pub_secret="jumpbox-admin-ssh-public-key-${env}"
  local priv_secret="jumpbox-admin-ssh-private-key-${env}"

  # Idempotent: if both secrets already exist, nothing to do.
  if az keyvault secret show --vault-name "${kv_name}" --name "${pub_secret}" --only-show-errors -o none 2>/dev/null \
     && az keyvault secret show --vault-name "${kv_name}" --name "${priv_secret}" --only-show-errors -o none 2>/dev/null; then
    echo "==> Key Vault already has ${pub_secret} and ${priv_secret} — skipping key seeding"
    return 0
  fi

  # If a real public key is already configured in tfvars, use it.
  # The private key belongs to whoever provided it and is not stored here.
  local tfvars_key
  tfvars_key="$(sed -nE 's/^[[:space:]]*jumpbox_admin_ssh_public_key[[:space:]]*=[[:space:]]*"([^"]+)"[[:space:]]*$/\1/p' "${var_file}" | head -n 1)"
  if [[ -n "${tfvars_key}" && "${tfvars_key}" != "<set-me-ssh-public-key>" ]]; then
    echo "==> Storing user-provided public key in Key Vault secret ${pub_secret}"
    az keyvault secret set --vault-name "${kv_name}" --name "${pub_secret}" \
      --value "${tfvars_key}" --only-show-errors -o none
    echo "==> NOTE: private key was user-provided and is not stored in Key Vault"
    return 0
  fi

  # Auto-generate a sandbox keypair.
  local tmp_key
  tmp_key="$(mktemp -u /tmp/sc3-jumpbox-XXXXXX)"
  # shellcheck disable=SC2064
  trap "rm -f '${tmp_key}' '${tmp_key}.pub'" RETURN
  ssh-keygen -q -t ed25519 -N '' -f "${tmp_key}"

  echo "==> Storing SSH public key in Key Vault secret ${pub_secret}"
  az keyvault secret set --vault-name "${kv_name}" --name "${pub_secret}" \
    --value "$(cat "${tmp_key}.pub")" --only-show-errors -o none

  echo "==> Storing SSH private key in Key Vault secret ${priv_secret}"
  az keyvault secret set --vault-name "${kv_name}" --name "${priv_secret}" \
    --file "${tmp_key}" --only-show-errors -o none

  echo "==> Keypair stored. To SSH into the jumpbox via Bastion, retrieve the private key:"
  echo "    az keyvault secret show --vault-name ${kv_name} --name ${priv_secret} --query value -o tsv > ~/.ssh/jumpbox-${env} && chmod 600 ~/.ssh/jumpbox-${env}"
}
# ---------------------------------------------------------------------------

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 2 plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  # Pre-apply: ensure the jumpbox SSH keypair is in Key Vault before Terraform
  # reads it to configure the VM. This must run before terraform apply so that
  # the data source for the public key secret does not fail.
  if [[ "${ENABLE_BOOTSTRAP_KEY_VAULT}" == "true" ]]; then
    BOOTSTRAP_KV_NAME="$(terraform -chdir="${ROOT_DIR}/infra/terraform/bootstrap" output -raw key_vault_name 2>/dev/null || true)"
    if [[ -z "${BOOTSTRAP_KV_NAME}" ]]; then
      echo "==> Skipping Key Vault key seeding: bootstrap key_vault_name output not found (run phase1 first)"
    else
      _seed_jumpbox_ssh_key "${BOOTSTRAP_KV_NAME}" "${ENVIRONMENT}" "${VAR_FILE}"
    fi
  fi

  echo "==> Running Phase 2 apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    "${EXTRA_VAR_FILE_ARGS[@]}" \
    -auto-approve \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 2 ${ACTION} completed for ${ENVIRONMENT}"
