#!/usr/bin/env bash
# Build the ingestion-runner Docker image and push it to the environment's ACR.
#
# Usage:
#   ENV=dev IMAGE_TAG=latest ./ops/scripts/build-push-ingestion.sh
#
# Prerequisites:
#   - Docker daemon running and authenticated to the registry
#   - Azure CLI authenticated (az login / workload identity)
#   - Terraform state initialised for the target environment (for output lookup)
#
# The script must run from inside the VNet (jumpbox or CI runner with VNet
# injection) because the ACR has public_network_access_enabled = false.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/runtime"

ENV="${ENV:-dev}"
LOCATION_SHORT="${LOCATION_SHORT:-aue}"
INSTANCE="${INSTANCE:-001}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE_REPOSITORY="ingestion-runner"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ai-platform-${ENV}}"

# ---------------------------------------------------------------------------
# Resolve ACR login server.
# Priority:
#   1. ACR_LOGIN_SERVER env var (explicit override)
#   2. terraform output (dev container with initialised state)
#   3. Derived directly from naming convention: acr<env><location_short><instance>
#      No API call required — avoids needing registries/read permission.
# ---------------------------------------------------------------------------
TF_DIR="${REPO_ROOT}/infra/terraform"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-}"

if [[ -z "${ACR_LOGIN_SERVER}" ]] && command -v terraform &>/dev/null; then
  pushd "${TF_DIR}" >/dev/null
  ACR_LOGIN_SERVER=$(terraform output -raw acr_login_server 2>/dev/null || true)
  popd >/dev/null
fi

if [[ -z "${ACR_LOGIN_SERVER}" ]]; then
  # Naming convention: acr<env><location_short><instance>.azurecr.io
  # Matches locals.tf: naming_suffix = "${environment}-${location_short}-${instance}"
  ACR_LOGIN_SERVER="acr${ENV}${LOCATION_SHORT}${INSTANCE}.azurecr.io"
  echo "INFO: Using derived ACR login server: ${ACR_LOGIN_SERVER}"
fi

FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_REPOSITORY}:${IMAGE_TAG}"

echo ""
echo "==> ACR:   ${ACR_LOGIN_SERVER}"
echo "==> Image: ${FULL_IMAGE}"
echo ""

# ---------------------------------------------------------------------------
# Authenticate to ACR using the current Azure CLI identity.
# ---------------------------------------------------------------------------
echo "==> Logging in to ACR…"
az acr login --name "${ACR_LOGIN_SERVER%%.*}"

# ---------------------------------------------------------------------------
# Build (linux/amd64 matches the Container App runtime).
# ---------------------------------------------------------------------------
echo "==> Building Docker image (context: ${RUNTIME_DIR})…"
docker build \
  --platform linux/amd64 \
  --tag "${FULL_IMAGE}" \
  "${RUNTIME_DIR}"

# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------
echo "==> Pushing image to ACR…"
docker push "${FULL_IMAGE}"

echo ""
echo "==> Done: ${FULL_IMAGE}"
echo ""
echo "Trigger ingestion job (files already in blob storage):"
echo "  az containerapp job start \\"
echo "    -n caj-ingestion-${ENV}-aue-001 \\"
echo "    -g rg-ai-platform-${ENV}"
echo ""
echo "Override to upload then index:"
echo "  az containerapp job start \\"
echo "    -n caj-ingestion-${ENV}-aue-001 \\"
echo "    -g rg-ai-platform-${ENV} \\"
echo "    --args '--mode' 'azure' '--input-dir' '/path/to/files'"
