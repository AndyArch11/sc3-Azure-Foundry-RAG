#!/usr/bin/env bash
set -euo pipefail

# Installs Terraform locally on Linux when Docker is not available.

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer currently supports Linux only."
  exit 1
fi

TERRAFORM_VERSION="${1:-1.14.7}"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64)
    TF_ARCH="amd64"
    ;;
  aarch64|arm64)
    TF_ARCH="arm64"
    ;;
  *)
    echo "Unsupported architecture: ${ARCH}"
    exit 1
    ;;
esac

URL="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TF_ARCH}.zip"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required."
  exit 1
fi

if ! command -v unzip >/dev/null 2>&1; then
  echo "unzip is required. Installing unzip via apt..."
  apt-get update && apt-get install -y --no-install-recommends unzip
fi

echo "==> Downloading Terraform ${TERRAFORM_VERSION}"
curl -fsSL "${URL}" -o "${TMP_DIR}/terraform.zip"

echo "==> Installing Terraform to ${INSTALL_DIR}"
unzip -q "${TMP_DIR}/terraform.zip" -d "${TMP_DIR}"
install -m 0755 "${TMP_DIR}/terraform" "${INSTALL_DIR}/terraform"

echo "==> Terraform installed"
terraform version
