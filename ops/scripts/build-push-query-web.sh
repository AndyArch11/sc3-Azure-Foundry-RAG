#!/usr/bin/env bash
# Build and push the rag-query-web image to the environment's ACR.
#
# Usage:
#   ENV=<env> IMAGE_TAG=<immutable-tag> ./ops/scripts/build-push-query-web.sh
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ENV=<env> IMAGE_TAG=<immutable-tag> ./ops/scripts/build-push-query-web.sh

Builds and pushes the rag-query-web image to the target environment ACR.

Recommended follow-up rollout:
  terraform -chdir=infra/terraform apply \
    -input=false \
    -var-file=environments/<env>/bootstrap.generated.tfvars \
    -var-file=environments/<env>/<env>.tfvars \
    -var query_web_image_tag=<immutable-tag> \
    -target=module.agent_hosting
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_DIR="${REPO_ROOT}/query_web"
TF_DIR="${REPO_ROOT}/infra/terraform"

if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
  echo "ERROR: Docker is not installed or the daemon is not running." >&2
  echo "Install Docker: curl -fsSL https://get.docker.com | sudo sh" >&2
  exit 1
fi

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

ENV="${ENV:-dev}"
LOCATION_SHORT="${LOCATION_SHORT:-aue}"
INSTANCE="${INSTANCE:-001}"
DEFAULT_TAG="$(date +%Y%m%d%H%M)-$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo local)"
IMAGE_TAG="${IMAGE_TAG:-${DEFAULT_TAG}}"
IMAGE_REPOSITORY="rag-query-web"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-}"

if [[ -z "${ACR_LOGIN_SERVER}" ]] && command -v terraform &>/dev/null; then
  pushd "${TF_DIR}" >/dev/null
  ACR_LOGIN_SERVER=$(terraform output -raw acr_login_server 2>/dev/null || true)
  popd >/dev/null
fi

if [[ -z "${ACR_LOGIN_SERVER}" ]]; then
  ACR_LOGIN_SERVER="acr${ENV}${LOCATION_SHORT}${INSTANCE}.azurecr.io"
  echo "INFO: Using derived ACR login server: ${ACR_LOGIN_SERVER}"
fi

FULL_IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_REPOSITORY}:${IMAGE_TAG}"

echo "==> ACR:   ${ACR_LOGIN_SERVER}"
echo "==> Image: ${FULL_IMAGE}"
if [[ "${IMAGE_TAG}" == "latest" ]]; then
  echo "WARNING: IMAGE_TAG=latest may cause stale revision rollouts in Container Apps."
  echo "Prefer immutable tags, e.g. IMAGE_TAG=${DEFAULT_TAG}."
fi

_assert_private_acr_resolution "${ACR_LOGIN_SERVER}"
_assert_private_acr_data_endpoint_if_enabled "${ACR_LOGIN_SERVER%%.*}"
az acr login --name "${ACR_LOGIN_SERVER%%.*}"

docker build --platform linux/amd64 --tag "${FULL_IMAGE}" "${APP_DIR}"
docker push "${FULL_IMAGE}"

echo "==> Done: ${FULL_IMAGE}"
echo "==> Rollout command:"
echo "terraform -chdir=${TF_DIR} apply -input=false -var-file=environments/${ENV}/bootstrap.generated.tfvars -var-file=environments/${ENV}/${ENV}.tfvars -var query_web_image_tag=${IMAGE_TAG} -target=module.agent_hosting"
