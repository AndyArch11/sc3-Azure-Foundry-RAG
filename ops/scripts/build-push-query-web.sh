#!/usr/bin/env bash
# Build and push the rag-query-web image to the environment's ACR.
#
# Usage:
#   ENV=dev IMAGE_TAG=latest ./ops/scripts/build-push-query-web.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_DIR="${REPO_ROOT}/query_web"
TF_DIR="${REPO_ROOT}/infra/terraform"

if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
  echo "ERROR: Docker is not installed or the daemon is not running." >&2
  echo "Install Docker: curl -fsSL https://get.docker.com | sudo sh" >&2
  exit 1
fi

ENV="${ENV:-dev}"
LOCATION_SHORT="${LOCATION_SHORT:-aue}"
INSTANCE="${INSTANCE:-001}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
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

az acr login --name "${ACR_LOGIN_SERVER%%.*}"

docker build --platform linux/amd64 --tag "${FULL_IMAGE}" "${APP_DIR}"
docker push "${FULL_IMAGE}"

echo "==> Done: ${FULL_IMAGE}"
