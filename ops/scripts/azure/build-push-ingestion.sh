#!/usr/bin/env bash
# Build the ingestion-runner Docker image and push it to the environment's ACR.
#
# Usage:
#   ENV=<env> IMAGE_TAG="$(date +%Y%m%d%H%M)-<gitsha>" ./ops/scripts/azure/build-push-ingestion.sh
#
# Prerequisites:
#   - Docker daemon running and authenticated to the registry
#   - Azure CLI authenticated (az login / workload identity)
#   - Terraform state initialised for the target environment (for output lookup)
#
# The script must run from inside the VNet (jumpbox or CI runner with VNet
# injection) because the ACR has public_network_access_enabled = false.
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ENV=<env> IMAGE_TAG=<immutable-tag> ./ops/scripts/azure/build-push-ingestion.sh

Builds and pushes the ingestion-runner image to the target environment ACR.

Recommended follow-up rollout:
  sudo ./ops/scripts/azure/rollout-agent-hosting.sh <env> apply \
    --ingestion-tag <immutable-tag>

Optional RBAC reconciliation (admin identity):
  ./ops/scripts/azure/reconcile-rbac-admin.sh <env> apply

Environment variable overrides:
  RUNTIME_REQUIREMENTS_FILE Docker build requirements profile
                   (default: requirements/ingestion.txt)
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/runtime"
TF_DIR="${REPO_ROOT}/infra/terraform/azure"

DOCKER_CMD=(docker)

_require_docker_access() {
  if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed." >&2
    echo "" >&2
    echo "Install Docker on Ubuntu:" >&2
    echo "  curl -fsSL https://get.docker.com | sudo sh" >&2
    echo "  sudo usermod -aG docker \$USER && newgrp docker" >&2
    exit 1
  fi

  if docker info >/dev/null 2>&1; then
    return 0
  fi

  local docker_info_error
  docker_info_error="$(docker info 2>&1 || true)"

  if grep -qiE "permission denied|/var/run/docker\.sock|got permission denied" <<<"${docker_info_error}"; then
    if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
      DOCKER_CMD=(sudo -n docker)
      echo "INFO: Docker requires elevated access in this shell; using 'sudo -n docker'."
      return 0
    fi

    echo "ERROR: Docker is installed, but this shell cannot access /var/run/docker.sock." >&2
    echo "" >&2
    echo "If Docker was just installed by configure-jumpbox.sh, refresh group membership:" >&2
    echo "  newgrp docker" >&2
    echo "  # or log out and log back in" >&2
    echo "" >&2
    echo "Raw docker info error:" >&2
    echo "${docker_info_error}" >&2
    exit 1
  fi

  if grep -qi "cannot connect to the docker daemon" <<<"${docker_info_error}"; then
    echo "ERROR: Docker daemon is not running or not reachable." >&2
    echo "Raw docker info error:" >&2
    echo "${docker_info_error}" >&2
    exit 1
  fi

  echo "ERROR: Docker preflight failed." >&2
  echo "Raw docker info error:" >&2
  echo "${docker_info_error}" >&2
  exit 1
}

_require_docker_access

if ! command -v az &>/dev/null; then
  echo "ERROR: Azure CLI (az) is required." >&2
  exit 1
fi

_ensure_az_login() {
  if az account show >/dev/null 2>&1; then
    return 0
  fi

  echo "INFO: Azure CLI not authenticated; attempting managed identity login..."
  if az login --identity --output none >/dev/null 2>&1; then
    echo "INFO: Azure CLI authenticated via managed identity."
    return 0
  fi

  echo "ERROR: Azure CLI is not authenticated. Run one of:" >&2
  echo "  az login" >&2
  echo "  az login --identity   # recommended on jumpbox VM" >&2
  exit 1
}

_is_private_ipv4() {
  local ip="$1"
  [[ "$ip" =~ ^10\. ]] || [[ "$ip" =~ ^192\.168\. ]] || [[ "$ip" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. ]]
}

_assert_private_acr_resolution() {
  local host="$1"
  local resolved_ip
  resolved_ip="$(getent ahostsv4 "$host" | awk '{print $1}' | head -n1 || true)"

  if [[ -z "$resolved_ip" ]]; then
    echo "WARNING: Could not resolve ${host}. Continuing, but push may fail if DNS is not configured." >&2
    return 0
  fi

  if ! _is_private_ipv4 "$resolved_ip"; then
    echo "ERROR: ${host} resolves to public IP ${resolved_ip}." >&2
    echo "This ACR has public access disabled and must resolve to a private endpoint IP from inside the VNet." >&2
    echo "Validate private DNS and VNet link for privatelink.azurecr.io, then retry." >&2
    exit 1
  fi

  echo "INFO: ${host} resolves privately (${resolved_ip})."
}

_assert_private_acr_data_endpoint_if_enabled() {
  local acr_name="$1"
  local data_enabled
  local data_host

  data_enabled="$(az acr show --name "$acr_name" --query dataEndpointEnabled -o tsv 2>/dev/null || true)"
  if [[ "$data_enabled" != "true" ]]; then
    return 0
  fi

  data_host="$(az acr show --name "$acr_name" --query 'dataEndpointHostNames[0]' -o tsv 2>/dev/null || true)"
  if [[ -z "$data_host" || "$data_host" == "null" ]]; then
    echo "WARNING: ACR data endpoint is enabled but hostname lookup via Azure CLI returned empty." >&2
    echo "Docker push may fail if the data endpoint does not resolve privately." >&2
    return 0
  fi

  _assert_private_acr_resolution "$data_host"
}

_ensure_az_login
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/runtime"

ENV="${ENV:-dev}"
LOCATION_SHORT="${LOCATION_SHORT:-aue}"
INSTANCE="${INSTANCE:-001}"
DEFAULT_TAG="$(date +%Y%m%d%H%M)-$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo local)"
IMAGE_TAG="${IMAGE_TAG:-${DEFAULT_TAG}}"
IMAGE_REPOSITORY="ingestion-runner"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ai-platform-${ENV}}"
RUNTIME_REQUIREMENTS_FILE="${RUNTIME_REQUIREMENTS_FILE:-}"
ENV_TFVARS_FILE="${TF_DIR}/environments/${ENV}/${ENV}.tfvars"

# ---------------------------------------------------------------------------
# Resolve ACR login server.
# Priority:
#   1. ACR_LOGIN_SERVER env var (explicit override)
#   2. terraform output (dev container with initialised state)
#   3. Derived directly from naming convention: acr<env><location_short><instance>
#      No API call required — avoids needing registries/read permission.
# ---------------------------------------------------------------------------
TF_DIR="${REPO_ROOT}/infra/terraform/azure"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-}"
JOB_NAME="${JOB_NAME:-}"

if [[ -z "${ACR_LOGIN_SERVER}" ]] && command -v terraform &>/dev/null; then
  pushd "${TF_DIR}" >/dev/null
  JOB_NAME=$(terraform output -raw container_app_job_name 2>/dev/null || true)
  RESOURCE_GROUP=$(terraform output -raw resource_group_name 2>/dev/null || true)
  ACR_LOGIN_SERVER=$(terraform output -raw acr_login_server 2>/dev/null || true)
  popd >/dev/null
fi

if [[ -z "${RESOURCE_GROUP}" ]]; then
  RESOURCE_GROUP="rg-ai-platform-${ENV}"
fi

if [[ -z "${JOB_NAME}" ]]; then
  JOB_NAME="caj-ingestion-${ENV}-${LOCATION_SHORT}-${INSTANCE}"
fi

if [[ -z "${ACR_LOGIN_SERVER}" ]]; then
  # Prefer explicit tfvars override when available.
  # This is resilient when terraform output is unavailable (for example, running
  # the script via sudo in a shell that does not have the same terraform context).
  if [[ -f "${ENV_TFVARS_FILE}" ]]; then
    ACR_NAME_OVERRIDE="$(grep -E '^acr_name_override\s*=\s*"' "${ENV_TFVARS_FILE}" | awk -F'"' '{print $2}' | head -1 || true)"
    if [[ -n "${ACR_NAME_OVERRIDE}" ]]; then
      ACR_LOGIN_SERVER="${ACR_NAME_OVERRIDE}.azurecr.io"
      echo "INFO: Using ACR login server from tfvars override: ${ACR_LOGIN_SERVER}"
    fi
  fi
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
if [[ "${IMAGE_TAG}" == "latest" ]]; then
  echo "WARNING: IMAGE_TAG=latest may cause stale revision rollouts in Container Apps."
  echo "Prefer immutable tags, e.g. IMAGE_TAG=${DEFAULT_TAG}."
fi

# ---------------------------------------------------------------------------
# Authenticate to ACR using the current Azure CLI identity.
# ---------------------------------------------------------------------------
_assert_private_acr_resolution "${ACR_LOGIN_SERVER}"
_assert_private_acr_data_endpoint_if_enabled "${ACR_LOGIN_SERVER%%.*}"
echo "==> Logging in to ACR…"
az acr login --name "${ACR_LOGIN_SERVER%%.*}"

# ---------------------------------------------------------------------------
# Build (linux/amd64 matches the Container App runtime).
# ---------------------------------------------------------------------------
echo "==> Building Docker image (context: ${RUNTIME_DIR})…"
BUILD_ARGS=()
if [[ -n "${RUNTIME_REQUIREMENTS_FILE}" ]]; then
  BUILD_ARGS+=(--build-arg "RUNTIME_REQUIREMENTS_FILE=${RUNTIME_REQUIREMENTS_FILE}")
fi
"${DOCKER_CMD[@]}" build \
  --platform linux/amd64 \
  "${BUILD_ARGS[@]}" \
  --tag "${FULL_IMAGE}" \
  "${RUNTIME_DIR}"

# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------
echo "==> Pushing image to ACR…"
"${DOCKER_CMD[@]}" push "${FULL_IMAGE}"

echo ""
echo "==> Done: ${FULL_IMAGE}"
echo ""
echo "==> Rollout command:"
echo "sudo ./ops/scripts/azure/rollout-agent-hosting.sh ${ENV} apply --ingestion-tag ${IMAGE_TAG}"
echo "# Optional admin RBAC reconciliation:"
echo "./ops/scripts/azure/reconcile-rbac-admin.sh ${ENV} apply"
echo ""
echo "Trigger ingestion job (files already in blob storage):"
echo "  az containerapp job start \\"
echo "    -n ${JOB_NAME} \\"
echo "    -g ${RESOURCE_GROUP}"
echo ""
echo "Override to upload then index:"
echo "  az containerapp job start \\"
echo "    -n ${JOB_NAME} \\"
echo "    -g ${RESOURCE_GROUP} \\"
echo "    --args '--mode' 'azure' '--input-dir' '/path/to/files'"
