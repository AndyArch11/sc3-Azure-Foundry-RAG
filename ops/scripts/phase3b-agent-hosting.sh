#!/usr/bin/env bash
set -euo pipefail

# Deploys agent hosting module separately from Phase 3 core, because
# hosted_query_agent uses a preview API (2025-04-01-preview) that can
# be slow to respond. Resources here carry 30-minute timeouts.

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

if ! command -v terraform >/dev/null 2>&1; then
  echo "Terraform is required in PATH."
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
terraform -chdir="${TF_DIR}" init -backend-config="${BACKEND_FILE}"

TARGET_ARGS=(
  "-target=module.agent_hosting"
)

# Safety-first defaults: serialize graph execution and wait for state lock.
TF_SAFETY_ARGS=(
  "-parallelism=1"
  "-lock-timeout=5m"
)

if [[ "${ACTION}" == "plan" ]]; then
  echo "==> Running Phase 3b agent-hosting plan (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" plan \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    -var='enable_hosted_query_agent_preview=true' \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
else
  echo "==> Running Phase 3b agent-hosting apply (${ENVIRONMENT})"
  terraform -chdir="${TF_DIR}" apply \
    -input=false \
    "${TF_SAFETY_ARGS[@]}" \
    -auto-approve \
    -var='enable_hosted_query_agent_preview=true' \
    -var-file="${VAR_FILE}" \
    "${TARGET_ARGS[@]}"
fi

echo "==> Phase 3b ${ACTION} completed for ${ENVIRONMENT}"
