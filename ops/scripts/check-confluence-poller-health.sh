#!/usr/bin/env bash
set -euo pipefail

# Check Confluence poller Container App status and tail recent logs.

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/check-confluence-poller-health.sh [env] [--resource-group <rg>] [--app-name <name>] [--lines <n>] [--follow]

Defaults:
  env   = dev
  lines = 100

Behaviour:
  - Resolves resource group and app name from terraform outputs when not provided.
  - Prints Container App summary and revision table.
  - Tails recent logs from the poller app.

Examples:
  ./ops/scripts/check-confluence-poller-health.sh
  ./ops/scripts/check-confluence-poller-health.sh dev --lines 200
  ./ops/scripts/check-confluence-poller-health.sh prod --follow
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform"

ENVIRONMENT="${1:-dev}"
if [[ $# -gt 0 && "${1}" != --* ]]; then
  shift
fi

RESOURCE_GROUP=""
APP_NAME=""
TAIL_LINES="100"
FOLLOW_LOGS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group)
      RESOURCE_GROUP="${2:-}"
      shift 2
      ;;
    --app-name)
      APP_NAME="${2:-}"
      shift 2
      ;;
    --lines)
      TAIL_LINES="${2:-100}"
      shift 2
      ;;
    --follow)
      FOLLOW_LOGS="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Use --help for usage."
      exit 1
      ;;
  esac
done

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required in PATH."
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Azure CLI is not authenticated. Run: az login"
  exit 1
fi

if ! [[ "${TAIL_LINES}" =~ ^[0-9]+$ ]]; then
  echo "--lines must be a positive integer"
  exit 1
fi

if [[ -z "${RESOURCE_GROUP}" ]]; then
  RESOURCE_GROUP="$(terraform -chdir="${TF_DIR}" output -raw resource_group_name 2>/dev/null || true)"
fi

if [[ -z "${APP_NAME}" ]]; then
  APP_NAME="$(terraform -chdir="${TF_DIR}" output -raw confluence_poller_app_name 2>/dev/null || true)"
fi

if [[ -z "${RESOURCE_GROUP}" ]]; then
  echo "Could not resolve resource group. Provide --resource-group or ensure terraform output is available."
  exit 2
fi

if [[ -z "${APP_NAME}" || "${APP_NAME}" == "null" ]]; then
  echo "Could not resolve confluence poller app name."
  echo "Provide --app-name or enable/deploy poller first."
  exit 2
fi

echo "==> Environment: ${ENVIRONMENT}"
echo "==> Resource group: ${RESOURCE_GROUP}"
echo "==> App name: ${APP_NAME}"

echo "==> Container App summary"
az containerapp show \
  --name "${APP_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --query '{name:name,provisioningState:properties.provisioningState,latestRevisionName:properties.latestRevisionName,fqdn:properties.configuration.ingress.fqdn}' \
  -o json

echo "==> Revisions"
az containerapp revision list \
  --name "${APP_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --query '[].{name:name,active:properties.active,healthState:properties.healthState,createdTime:properties.createdTime}' \
  -o table

echo "==> Recent logs (tail=${TAIL_LINES})"
LOG_ARGS=(
  --name "${APP_NAME}"
  --resource-group "${RESOURCE_GROUP}"
  --tail "${TAIL_LINES}"
)
if [[ "${FOLLOW_LOGS}" == "true" ]]; then
  LOG_ARGS+=(--follow)
fi

if ! az containerapp logs show "${LOG_ARGS[@]}"; then
  echo "WARNING: Could not fetch container logs. Verify app exists and caller has access."
  exit 3
fi
