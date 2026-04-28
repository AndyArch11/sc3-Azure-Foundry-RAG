#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers — fall back gracefully in non-interactive terminals.
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; BOLD=''; RESET=''
fi

info()  { echo -e "${GREEN}==>${RESET} $*"; }
warn()  { echo -e "${YELLOW}SKIP:${RESET} $*"; }
error() { echo -e "${RED}ERROR:${RESET} $*" >&2; }

# ---------------------------------------------------------------------------
# Smoke-test result tracking.
# ---------------------------------------------------------------------------
SMOKE_RESULTS=()   # "label:PASS|SKIP|FAIL"

smoke_record() {
  local label="$1"
  local result="$2"   # PASS | SKIP | FAIL
  SMOKE_RESULTS+=("${label}:${result}")
}

smoke_report() {
  echo ""
  echo -e "${BOLD}Smoke test results${RESET}"
  local any_fail="false"
  for entry in "${SMOKE_RESULTS[@]}"; do
    local label="${entry%%:*}"
    local result="${entry##*:}"
    case "${result}" in
      PASS) echo -e "  ${GREEN}PASS${RESET}  ${label}" ;;
      SKIP) echo -e "  ${YELLOW}SKIP${RESET}  ${label}" ;;
      FAIL) echo -e "  ${RED}FAIL${RESET}  ${label}"; any_fail="true" ;;
    esac
  done
  echo ""
  if [[ "${any_fail}" == "true" ]]; then
    error "One or more smoke checks failed."
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Help text.
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/configure-jumpbox.sh [options]

Configures an Ubuntu jumpbox for local platform operations by:
  - installing OS packages (Docker, Python, Azure CLI, etc.)
  - creating runtime/.venv
  - installing Python dependencies for runtime or repo unit tests
  - optionally installing Terraform locally
  - optionally performing az login --identity and running unit tests
  - running a smoke check across all installed components

Options:
  --repo-dir <path>         Repository root to configure (default: current repo root)
  --python-version <ver>    Python version to install and use for the venv (default: 3.12)
  --runtime-only            Install runtime/requirements.txt instead of requirements-dev.txt
  --install-terraform       Run ops/scripts/install-terraform-local.sh
  --terraform-version <ver> Terraform version passed to install-terraform-local.sh
  --init-terraform-backend <env>
                           Run 'terraform init -backend-config=infra/terraform/environments/<env>/backend.hcl'
                           after az login. Supported env: dev, test, prod.
  --install-azure-cli       Install Azure CLI via the Microsoft apt repository
  --az-login-identity       Run 'az login --identity' after setup (requires managed identity)
  --az-login-client-id <id> Client ID for user-assigned managed identity used with --az-login-identity.
                           If omitted, the script attempts auto-discovery from Azure IMDS.
  --run-unit-tests          Run 'pytest tests/unit -q' after setup
  --skip-docker             Skip Docker installation
  --skip-apt-update         Skip apt-get update and package installation

Examples:
  ./ops/scripts/configure-jumpbox.sh --install-terraform
  ./ops/scripts/configure-jumpbox.sh --install-terraform --install-azure-cli --az-login-identity --az-login-client-id <client-id> --init-terraform-backend dev --run-unit-tests
  ./ops/scripts/configure-jumpbox.sh --repo-dir /opt/sc3-ingestion --install-terraform
  ./ops/scripts/configure-jumpbox.sh --runtime-only
EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# Defaults.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REPO_DIR="${DEFAULT_REPO_ROOT}"
PYTHON_VERSION="3.12"
INSTALL_RUNTIME_ONLY="false"
INSTALL_TERRAFORM="false"
TERRAFORM_VERSION=""
INIT_TERRAFORM_BACKEND_ENV=""
INSTALL_AZURE_CLI="false"
AZ_LOGIN_IDENTITY="false"
AZ_LOGIN_CLIENT_ID=""
RUN_UNIT_TESTS="false"
SKIP_DOCKER="false"
SKIP_APT_UPDATE="false"

# ---------------------------------------------------------------------------
# Argument parsing.
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --python-version)
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --runtime-only)
      INSTALL_RUNTIME_ONLY="true"
      shift
      ;;
    --install-terraform)
      INSTALL_TERRAFORM="true"
      shift
      ;;
    --terraform-version)
      TERRAFORM_VERSION="$2"
      shift 2
      ;;
    --init-terraform-backend)
      INIT_TERRAFORM_BACKEND_ENV="$2"
      shift 2
      ;;
    --install-azure-cli)
      INSTALL_AZURE_CLI="true"
      shift
      ;;
    --az-login-identity)
      AZ_LOGIN_IDENTITY="true"
      shift
      ;;
    --az-login-client-id)
      AZ_LOGIN_CLIENT_ID="$2"
      shift 2
      ;;
    --run-unit-tests)
      RUN_UNIT_TESTS="true"
      shift
      ;;
    --skip-docker)
      SKIP_DOCKER="true"
      shift
      ;;
    --skip-apt-update)
      SKIP_APT_UPDATE="true"
      shift
      ;;
    *)
      error "Unknown argument: $1"
      echo "Run with --help for usage."
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------
if [[ "$(uname -s)" != "Linux" ]]; then
  error "This script currently supports Linux only."
  exit 1
fi

if [[ ! -d "${REPO_DIR}" ]]; then
  error "Repository directory not found: ${REPO_DIR}"
  exit 1
fi

RUNTIME_DIR="${REPO_DIR}/runtime"
VENV_DIR="${RUNTIME_DIR}/.venv"
PYTHON_BIN="python${PYTHON_VERSION}"

if [[ "${INSTALL_RUNTIME_ONLY}" == "true" ]]; then
  REQUIREMENTS_FILE="${RUNTIME_DIR}/requirements.txt"
else
  REQUIREMENTS_FILE="${REPO_DIR}/requirements-dev.txt"
fi

if [[ ! -d "${RUNTIME_DIR}" ]]; then
  error "Runtime directory not found: ${RUNTIME_DIR}"
  exit 1
fi

if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
  error "Requirements file not found: ${REQUIREMENTS_FILE}"
  exit 1
fi

if [[ -n "${TERRAFORM_VERSION}" && "${INSTALL_TERRAFORM}" != "true" ]]; then
  error "--terraform-version requires --install-terraform"
  exit 1
fi

if [[ -n "${INIT_TERRAFORM_BACKEND_ENV}" ]]; then
  case "${INIT_TERRAFORM_BACKEND_ENV}" in
    dev|test|prod)
      ;;
    *)
      error "--init-terraform-backend must be one of: dev, test, prod"
      exit 1
      ;;
  esac
fi

if [[ "${AZ_LOGIN_IDENTITY}" == "true" && "${INSTALL_AZURE_CLI}" != "true" ]]; then
  # Allow if az is already installed
  if ! command -v az >/dev/null 2>&1; then
    error "--az-login-identity requires Azure CLI. Add --install-azure-cli or install it first."
    exit 1
  fi
fi

if [[ -n "${AZ_LOGIN_CLIENT_ID}" && "${AZ_LOGIN_IDENTITY}" != "true" ]]; then
  error "--az-login-client-id requires --az-login-identity"
  exit 1
fi

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

# ---------------------------------------------------------------------------
# OS PACKAGES
# ---------------------------------------------------------------------------
apt_install() {
  if [[ "${SKIP_APT_UPDATE}" == "true" ]]; then
    warn "apt-get update skipped (--skip-apt-update)"
    return
  fi

  info "Updating apt and installing base OS packages"
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    git \
    gnupg \
    lsb-release \
    software-properties-common \
    unzip
}

# ---------------------------------------------------------------------------
# PYTHON
# ---------------------------------------------------------------------------
ensure_python() {
  if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    warn "Python ${PYTHON_VERSION} already installed — skipping"
    return
  fi

  if [[ "${SKIP_APT_UPDATE}" == "true" ]]; then
    error "${PYTHON_BIN} is required but apt installation was skipped."
    exit 1
  fi

  if ! apt-cache show "python${PYTHON_VERSION}" >/dev/null 2>&1; then
    info "Adding deadsnakes PPA for Python ${PYTHON_VERSION}"
    ${SUDO} add-apt-repository ppa:deadsnakes/ppa -y
    ${SUDO} apt-get update
  fi

  info "Installing Python ${PYTHON_VERSION}"
  ${SUDO} apt-get install -y --no-install-recommends \
    "python${PYTHON_VERSION}" \
    "python${PYTHON_VERSION}-venv" \
    python3-pip
}

# ---------------------------------------------------------------------------
# DOCKER
# ---------------------------------------------------------------------------
install_docker() {
  if [[ "${SKIP_DOCKER}" == "true" ]]; then
    warn "Docker installation skipped (--skip-docker)"
    return
  fi

  if command -v docker >/dev/null 2>&1; then
    warn "Docker already installed — skipping"
  else
    info "Installing Docker"
    curl -fsSL https://get.docker.com | ${SUDO} sh
  fi

  if id -nG "${USER}" | grep -qw docker; then
    return
  fi

  info "Adding ${USER} to docker group"
  ${SUDO} usermod -aG docker "${USER}"
  echo "NOTE: Start a new shell or run 'newgrp docker' before using Docker without sudo."
}

# ---------------------------------------------------------------------------
# AZURE CLI
# ---------------------------------------------------------------------------
install_azure_cli() {
  if [[ "${INSTALL_AZURE_CLI}" != "true" ]]; then
    warn "Azure CLI installation skipped (omit --install-azure-cli to keep this skipped)"
    return
  fi

  if command -v az >/dev/null 2>&1; then
    warn "Azure CLI already installed — skipping"
    return
  fi

  info "Installing Azure CLI"
  curl -sL https://aka.ms/InstallAzureCLIDeb | ${SUDO} bash
}

# ---------------------------------------------------------------------------
# TERRAFORM
# ---------------------------------------------------------------------------
install_terraform() {
  if [[ "${INSTALL_TERRAFORM}" != "true" ]]; then
    warn "Terraform installation skipped (use --install-terraform to enable)"
    return
  fi

  if command -v terraform >/dev/null 2>&1 && [[ -z "${TERRAFORM_VERSION}" ]]; then
    warn "Terraform already installed ($(terraform version -json 2>/dev/null | grep -o '"[0-9.]*"' | head -1 | tr -d '"')) — skipping"
    return
  fi

  info "Installing Terraform"
  if [[ -n "${TERRAFORM_VERSION}" ]]; then
    "${REPO_DIR}/ops/scripts/install-terraform-local.sh" "${TERRAFORM_VERSION}"
  else
    "${REPO_DIR}/ops/scripts/install-terraform-local.sh"
  fi
}

# ---------------------------------------------------------------------------
# PYTHON VENV
# ---------------------------------------------------------------------------
setup_venv() {
  info "Creating virtual environment: ${VENV_DIR}"
  rm -rf "${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"

  info "Upgrading pip tooling"
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

  info "Installing Python dependencies from ${REQUIREMENTS_FILE}"
  "${VENV_DIR}/bin/python" -m pip install -r "${REQUIREMENTS_FILE}"
}

# ---------------------------------------------------------------------------
# AZ LOGIN
# ---------------------------------------------------------------------------
az_login() {
  if [[ "${AZ_LOGIN_IDENTITY}" != "true" ]]; then
    warn "az login --identity skipped (use --az-login-identity to enable)"
    return
  fi

  _discover_uami_client_ids() {
    local imds_url="http://169.254.169.254/metadata/identity/info?api-version=2018-02-01"
    local payload
    payload="$(curl -fsS -H Metadata:true "${imds_url}" 2>/dev/null || true)"
    if [[ -z "${payload}" ]]; then
      return 0
    fi

    python3 - <<'PY' <<<"${payload}"
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

identity = data.get("identity", {}) if isinstance(data, dict) else {}
user_assigned = identity.get("userAssignedIdentities", [])

if isinstance(user_assigned, dict):
    # Some API shapes may return a dict keyed by resource ID.
    values = user_assigned.values()
elif isinstance(user_assigned, list):
    values = user_assigned
else:
    values = []

for item in values:
    if not isinstance(item, dict):
        continue
    client_id = item.get("clientId") or item.get("client_id")
    if isinstance(client_id, str) and client_id.strip():
        print(client_id.strip())
PY
  }

  if [[ -n "${AZ_LOGIN_CLIENT_ID}" ]]; then
    info "Authenticating with user-assigned managed identity (az login --identity --username ${AZ_LOGIN_CLIENT_ID})"
    az login --identity --username "${AZ_LOGIN_CLIENT_ID}"
  else
    local discovered_client_ids=()
    while IFS= read -r line; do
      [[ -n "${line}" ]] && discovered_client_ids+=("${line}")
    done < <(_discover_uami_client_ids)

    if [[ ${#discovered_client_ids[@]} -eq 1 ]]; then
      AZ_LOGIN_CLIENT_ID="${discovered_client_ids[0]}"
      info "Auto-detected user-assigned managed identity client ID: ${AZ_LOGIN_CLIENT_ID}"
      info "Authenticating with user-assigned managed identity (az login --identity --username ${AZ_LOGIN_CLIENT_ID})"
      az login --identity --username "${AZ_LOGIN_CLIENT_ID}"
    elif [[ ${#discovered_client_ids[@]} -gt 1 ]]; then
      error "Multiple user-assigned managed identities detected on this VM. Provide --az-login-client-id explicitly."
      echo "Discovered client IDs:"
      for cid in "${discovered_client_ids[@]}"; do
        echo "  - ${cid}"
      done
      exit 1
    else
      info "Authenticating with managed identity (az login --identity)"
      az login --identity
    fi
  fi
}

# ---------------------------------------------------------------------------
# UNIT TESTS
# ---------------------------------------------------------------------------
run_unit_tests() {
  if [[ "${RUN_UNIT_TESTS}" != "true" ]]; then
    warn "Unit tests skipped (use --run-unit-tests to enable)"
    return
  fi

  info "Running unit tests"
  local -a pytest_cache_args=()

  # If script is run with sudo/root, avoid creating root-owned .pytest_cache in
  # the repository, which later causes warnings for non-root jumpbox users.
  if [[ "${EUID}" -eq 0 ]]; then
    local fallback_cache_dir
    fallback_cache_dir="${TMPDIR:-/tmp}/pytest-cache-${SUDO_USER:-root}"
    mkdir -p "${fallback_cache_dir}"
    pytest_cache_args=("-o" "cache_dir=${fallback_cache_dir}")
  fi

  "${VENV_DIR}/bin/python" -m pytest "${REPO_DIR}/tests/unit" -q "${pytest_cache_args[@]}"
}

# ---------------------------------------------------------------------------
# TERRAFORM BACKEND INIT
# ---------------------------------------------------------------------------
init_terraform_backend() {
  if [[ -z "${INIT_TERRAFORM_BACKEND_ENV}" ]]; then
    warn "Terraform backend init skipped (use --init-terraform-backend <env> to enable)"
    return
  fi

  if ! command -v terraform >/dev/null 2>&1; then
    error "Terraform is required for backend init. Add --install-terraform or install it first."
    exit 1
  fi

  if ! command -v az >/dev/null 2>&1; then
    error "Azure CLI is required for backend init. Add --install-azure-cli or install it first."
    exit 1
  fi

  if ! az account show >/dev/null 2>&1; then
    error "Azure CLI is not authenticated. Use --az-login-identity or run az login before backend init."
    exit 1
  fi

  local tf_dir="${REPO_DIR}/infra/terraform"
  local backend_file="${tf_dir}/environments/${INIT_TERRAFORM_BACKEND_ENV}/backend.hcl"
  local backend_rg=""
  local backend_sa=""

  if [[ ! -f "${backend_file}" ]]; then
    error "Backend config not found: ${backend_file}. Run phase1-bootstrap.sh ${INIT_TERRAFORM_BACKEND_ENV} first."
    exit 1
  fi

  backend_rg="$(sed -nE 's/^[[:space:]]*resource_group_name[[:space:]]*=[[:space:]]*"([^"]+)"[[:space:]]*$/\1/p' "${backend_file}" | head -n 1)"
  backend_sa="$(sed -nE 's/^[[:space:]]*storage_account_name[[:space:]]*=[[:space:]]*"([^"]+)"[[:space:]]*$/\1/p' "${backend_file}" | head -n 1)"

  if [[ -z "${backend_rg}" || -z "${backend_sa}" ]]; then
    error "Unable to parse backend config values from ${backend_file}."
    exit 1
  fi

  if ! az storage account show --name "${backend_sa}" --resource-group "${backend_rg}" --only-show-errors -o none >/dev/null 2>&1; then
    local sub_id
    local assignee
    sub_id="$(az account show --query id -o tsv 2>/dev/null || true)"
    assignee="$(az account show --query user.name -o tsv 2>/dev/null || true)"

    error "Current identity cannot read Terraform backend storage account '${backend_sa}' in resource group '${backend_rg}'."
    echo "Grant RBAC on scope /subscriptions/${sub_id}/resourceGroups/${backend_rg}/providers/Microsoft.Storage/storageAccounts/${backend_sa} and retry."
    echo "Recommended roles for Terraform backend access:"
    echo "  - Reader"
    echo "  - Storage Blob Data Contributor"
    if [[ -n "${assignee}" ]]; then
      echo "Current az account user.name: ${assignee}"
    fi
    exit 1
  fi

  info "Initialising Terraform backend for ${INIT_TERRAFORM_BACKEND_ENV}"
  terraform -chdir="${tf_dir}" init -reconfigure -backend-config="${backend_file}"
}

# ---------------------------------------------------------------------------
# SMOKE TESTS — verify every component after setup
# ---------------------------------------------------------------------------
run_smoke_tests() {
  info "Running smoke checks"

  # Python
  if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    smoke_record "python${PYTHON_VERSION}" "PASS"
  else
    smoke_record "python${PYTHON_VERSION}" "FAIL"
  fi

  # venv python executable
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    smoke_record "venv (${VENV_DIR})" "PASS"
  else
    smoke_record "venv (${VENV_DIR})" "FAIL"
  fi

  # pytest importable inside venv
  if "${VENV_DIR}/bin/python" -m pytest --version >/dev/null 2>&1; then
    smoke_record "pytest (in venv)" "PASS"
  else
    smoke_record "pytest (in venv)" "FAIL"
  fi

  # Docker
  if [[ "${SKIP_DOCKER}" == "true" ]]; then
    smoke_record "docker" "SKIP"
  elif command -v docker >/dev/null 2>&1; then
    smoke_record "docker" "PASS"
  else
    smoke_record "docker" "FAIL"
  fi

  # Azure CLI
  if [[ "${INSTALL_AZURE_CLI}" != "true" ]] && ! command -v az >/dev/null 2>&1; then
    smoke_record "azure-cli" "SKIP"
  elif command -v az >/dev/null 2>&1; then
    smoke_record "azure-cli" "PASS"
  else
    smoke_record "azure-cli" "FAIL"
  fi

  # Terraform
  if [[ "${INSTALL_TERRAFORM}" != "true" ]]; then
    smoke_record "terraform" "SKIP"
  elif command -v terraform >/dev/null 2>&1; then
    smoke_record "terraform" "PASS"
  else
    smoke_record "terraform" "FAIL"
  fi

  # Terraform backend init status
  if [[ -z "${INIT_TERRAFORM_BACKEND_ENV}" ]]; then
    smoke_record "terraform backend init" "SKIP"
  elif [[ -d "${REPO_DIR}/infra/terraform/.terraform" ]]; then
    smoke_record "terraform backend init" "PASS"
  else
    smoke_record "terraform backend init" "FAIL"
  fi

  # azure-cosmos importable (key dependency for the conversation management tests)
  if "${VENV_DIR}/bin/python" -c "import azure.cosmos" >/dev/null 2>&1; then
    smoke_record "azure-cosmos (importable)" "PASS"
  else
    smoke_record "azure-cosmos (importable)" "FAIL"
  fi

  # az login / identity (only when requested)
  if [[ "${AZ_LOGIN_IDENTITY}" != "true" ]]; then
    smoke_record "az login --identity" "SKIP"
  elif az account show >/dev/null 2>&1; then
    smoke_record "az login --identity" "PASS"
  else
    smoke_record "az login --identity" "FAIL"
  fi

  smoke_report
}

# ---------------------------------------------------------------------------
# Main execution sequence
# ---------------------------------------------------------------------------
apt_install
ensure_python
install_docker
install_azure_cli
install_terraform
setup_venv
az_login
init_terraform_backend
run_unit_tests
run_smoke_tests

info "Jumpbox configuration completed"
echo "   repo_dir=${REPO_DIR}"
echo "   python=${PYTHON_BIN}"
echo "   venv=${VENV_DIR}"
echo "   requirements=${REQUIREMENTS_FILE}"
echo "   terraform=$( [[ "${INSTALL_TERRAFORM}" == "true" ]] && echo installed || echo skipped )"
echo "   terraform-backend=$( [[ -n "${INIT_TERRAFORM_BACKEND_ENV}" ]] && echo "init:${INIT_TERRAFORM_BACKEND_ENV}" || echo skipped )"
echo "   azure-cli=$( [[ "${INSTALL_AZURE_CLI}" == "true" ]] && echo installed || echo skipped )"
echo "   az-login=$( [[ "${AZ_LOGIN_IDENTITY}" == "true" ]] && echo done || echo skipped )"
echo "   az-login-client-id=$( [[ -n "${AZ_LOGIN_CLIENT_ID}" ]] && echo "${AZ_LOGIN_CLIENT_ID}" || echo auto )"
echo "   unit-tests=$( [[ "${RUN_UNIT_TESTS}" == "true" ]] && echo run || echo skipped )"