#!/usr/bin/env bash
# Build the Confluence poller image and push it to the environment's ECR repository.
#
# Usage:
#   ENV=<env> IMAGE_TAG=<immutable-tag> ./ops/scripts/aws/build-push-confluence-poller.sh
#
# Prerequisites:
#   - Docker daemon running
#   - AWS CLI authenticated (aws configure / instance profile / OIDC)
#   - Terraform state initialised for the target environment (used for ECR URL lookup)
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ENV=<env> IMAGE_TAG=<immutable-tag> ./ops/scripts/aws/build-push-confluence-poller.sh

Builds and pushes the confluence-poller image to the target environment ECR repository.

Recommended follow-up rollout:
  ./ops/scripts/aws/rollout-app-hosting.sh <env> apply --confluence-poller-tag <immutable-tag> --enable-confluence-poller

Environment variable overrides:
  ENV              Target environment (default: dev)
  AWS_REGION       AWS region         (default: ap-southeast-2)
  IMAGE_TAG        Image tag          (default: <timestamp>-<gitsha>)
  ECR_REPO_URL     ECR repository URL (default: resolved from terraform output)
  RUNTIME_REQUIREMENTS_FILE Docker build requirements profile
                   (default: /app/runtime-requirements/poller.txt)
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TF_DIR="${REPO_ROOT}/infra/terraform/aws"

DOCKER_CMD=(docker)

_require_docker_access() {
  if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed." >&2
    echo "Install Docker: curl -fsSL https://get.docker.com | sudo sh" >&2
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
    echo "ERROR: Docker is installed but cannot access /var/run/docker.sock." >&2
    echo "Run 'newgrp docker' or re-login after adding yourself to the docker group." >&2
    exit 1
  fi

  echo "ERROR: Docker daemon is not running or not reachable." >&2
  exit 1
}

_require_docker_access

if ! command -v aws &>/dev/null; then
  echo "ERROR: AWS CLI (aws) is required." >&2
  exit 1
fi

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not authenticated." >&2
  echo "Run: aws configure  (or configure an instance profile / OIDC)" >&2
  exit 1
fi

ENV="${ENV:-dev}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
DEFAULT_TAG="$(date +%Y%m%d%H%M)-$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo local)"
IMAGE_TAG="${IMAGE_TAG:-${DEFAULT_TAG}}"
ECR_REPO_URL="${ECR_REPO_URL:-}"
RUNTIME_REQUIREMENTS_FILE="${RUNTIME_REQUIREMENTS_FILE:-}"
TF_VARS_FILE="${TF_DIR}/environments/${ENV}/${ENV}.tfvars"

_read_tfvars_value() {
  local key="$1"
  local file="$2"
  [[ -f "${file}" ]] || return 1
  sed -nE "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"([^\"]+)\"[[:space:]]*$/\1/p" "${file}" | head -n 1
}

if [[ -z "${ECR_REPO_URL}" ]] && command -v terraform &>/dev/null; then
  pushd "${TF_DIR}" >/dev/null
  ECR_REPO_URL="$(terraform output -raw confluence_poller_repository_url 2>/dev/null || true)"
  popd >/dev/null
fi

if [[ -z "${ECR_REPO_URL}" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  TF_PROJECT="${TF_PROJECT:-$(_read_tfvars_value project "${TF_VARS_FILE}" || true)}"
  TF_PROJECT="${TF_PROJECT:-rag}"
  AWS_REGION_SHORT="${AWS_REGION_SHORT:-$(_read_tfvars_value aws_region_short "${TF_VARS_FILE}" || true)}"
  if [[ -z "${AWS_REGION_SHORT}" ]]; then
    echo "ERROR: Could not determine aws_region_short for fallback ECR naming." >&2
    echo "Set AWS_REGION_SHORT, ECR_REPO_URL, or ensure ${TF_VARS_FILE} exists with aws_region_short." >&2
    exit 1
  fi
  NAMING_SUFFIX="${TF_PROJECT}-${ENV}-${AWS_REGION_SHORT}"
  ECR_REPO_URL="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${NAMING_SUFFIX}/confluence-poller"
  echo "INFO: ECR URL not found in Terraform outputs; using derived URL: ${ECR_REPO_URL}"
fi

ECR_REGISTRY="${ECR_REPO_URL%%/*}"
FULL_IMAGE="${ECR_REPO_URL}:${IMAGE_TAG}"

echo "==> ECR registry : ${ECR_REGISTRY}"
echo "==> Image        : ${FULL_IMAGE}"

if [[ "${IMAGE_TAG}" == "latest" ]]; then
  echo "WARNING: IMAGE_TAG=latest may cause stale ECS deployments. Prefer immutable tags, e.g. IMAGE_TAG=${DEFAULT_TAG}."
fi

echo "==> Authenticating Docker to ECR"
aws ecr get-login-password --region "${AWS_REGION}" \
  | "${DOCKER_CMD[@]}" login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "==> Building image"
BUILD_ARGS=()
if [[ -n "${RUNTIME_REQUIREMENTS_FILE}" ]]; then
  BUILD_ARGS+=(--build-arg "RUNTIME_REQUIREMENTS_FILE=${RUNTIME_REQUIREMENTS_FILE}")
fi
"${DOCKER_CMD[@]}" build \
  --platform linux/amd64 \
  --file "${REPO_ROOT}/runtime/Dockerfile.poller" \
  "${BUILD_ARGS[@]}" \
  --tag "${FULL_IMAGE}" \
  "${REPO_ROOT}"

echo "==> Pushing image"
"${DOCKER_CMD[@]}" push "${FULL_IMAGE}"

echo ""
echo "==> Done: ${FULL_IMAGE}"
echo ""
echo "==> Rollout command:"
echo "    ./ops/scripts/aws/rollout-app-hosting.sh ${ENV} apply --confluence-poller-tag ${IMAGE_TAG} --enable-confluence-poller"
