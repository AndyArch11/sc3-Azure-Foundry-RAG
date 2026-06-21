#!/usr/bin/env bash
# Bootstraps AWS remote Terraform state (S3 bucket + DynamoDB lock table) and
# writes the backend configuration file consumed by subsequent terraform init calls.
#
# Usage:
#   ./ops/scripts/aws/phase1-bootstrap.sh <env>
#
# Environment variable overrides:
#   AWS_REGION           (default: ap-southeast-2)
#   TF_PROJECT           (default: rag)
#   TF_BACKEND_KEY       (default: aws/<env>/terraform.tfstate)
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/aws/phase1-bootstrap.sh <env>

Bootstraps AWS remote Terraform state and writes:
  - infra/terraform/aws/environments/<env>/backend.hcl

Supported environments:
  dev, test, prod

Optional env var overrides:
  AWS_REGION     Target AWS region           (default: ap-southeast-2)
  TF_PROJECT     Project name in naming      (default: rag)
  TF_BACKEND_KEY S3 key for main stack state (default: aws/<env>/terraform.tfstate)
  TF_ENABLE_BOOTSTRAP_SECRETS_MANAGER       (default: true)
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BOOTSTRAP_DIR="${ROOT_DIR}/infra/terraform/aws/bootstrap"

ENVIRONMENT="${1:-dev}"
case "${ENVIRONMENT}" in
  dev|test|prod) ;;
  *)
    echo "Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod." >&2
    exit 1
    ;;
esac

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
TF_PROJECT="${TF_PROJECT:-rag}"
TF_BACKEND_KEY="${TF_BACKEND_KEY:-aws/${ENVIRONMENT}/terraform.tfstate}"
TF_ENABLE_BOOTSTRAP_SECRETS_MANAGER="${TF_ENABLE_BOOTSTRAP_SECRETS_MANAGER:-true}"
BACKEND_FILE="${ROOT_DIR}/infra/terraform/aws/environments/${ENVIRONMENT}/backend.hcl"
DEFAULT_LOCK_TABLE_NAME="tfstate-lock-${TF_PROJECT}-${ENVIRONMENT}"
LOCK_TABLE_NAME="${DEFAULT_LOCK_TABLE_NAME}"

has_lock_table_in_state() {
  terraform state list | grep -qx "aws_dynamodb_table.lock"
}

import_lock_table_if_needed() {
  if has_lock_table_in_state; then
    return 0
  fi

  echo "    Importing existing lock table '${LOCK_TABLE_NAME}' into Terraform state."
  terraform import -input=false aws_dynamodb_table.lock "${LOCK_TABLE_NAME}" >/dev/null
}

get_state_lock_table_name() {
  terraform state show -no-color aws_dynamodb_table.lock 2>/dev/null \
    | awk -F'=' '/^[[:space:]]*name[[:space:]]*=/{gsub(/[ "]/, "", $2); print $2; exit}'
}

reconcile_lock_table_state() {
  # Self-heal stale local Terraform state when the lock table drifted out-of-band.
  if ! terraform state list >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Validating lock table state consistency"

  local state_lock_table_name
  local state_table_status
  if has_lock_table_in_state; then
    state_lock_table_name="$(get_state_lock_table_name)"
    if [[ -n "${state_lock_table_name}" ]]; then
      if state_table_status="$(aws dynamodb describe-table \
        --table-name "${state_lock_table_name}" \
        --region "${AWS_REGION}" \
        --query 'Table.TableStatus' \
        --output text 2>&1)"; then
        if [[ "${state_table_status}" == "ACTIVE" ]]; then
          LOCK_TABLE_NAME="${state_lock_table_name}"
          echo "    Reusing managed lock table '${LOCK_TABLE_NAME}'."
          return 0
        fi

        echo "    Managed lock table '${state_lock_table_name}' status is '${state_table_status}'. Replacing."
      elif ! echo "${state_table_status}" | grep -qi "ResourceNotFoundException"; then
        echo "WARNING: Could not validate managed lock table; continuing without self-heal." >&2
        echo "         AWS error: ${state_table_status}" >&2
        return 0
      fi

      terraform state rm aws_dynamodb_table.lock >/dev/null
    fi
  fi

  local describe_output
  if describe_output="$(aws dynamodb describe-table \
    --table-name "${DEFAULT_LOCK_TABLE_NAME}" \
    --region "${AWS_REGION}" \
    --query 'Table.TableStatus' \
    --output text 2>&1)"; then
    if [[ "${describe_output}" == "ACTIVE" ]]; then
      LOCK_TABLE_NAME="${DEFAULT_LOCK_TABLE_NAME}"
      echo "    Lock table '${LOCK_TABLE_NAME}' is ACTIVE."
      import_lock_table_if_needed
      return 0
    fi

    if [[ "${describe_output}" == "ARCHIVED" ]]; then
      LOCK_TABLE_NAME="${DEFAULT_LOCK_TABLE_NAME}-$(date +%Y%m%d%H%M%S)"
      echo "    Lock table '${DEFAULT_LOCK_TABLE_NAME}' is ARCHIVED and cannot be used for locking."
      echo "    Selecting new lock table name '${LOCK_TABLE_NAME}'."
    else
      LOCK_TABLE_NAME="${DEFAULT_LOCK_TABLE_NAME}-$(date +%Y%m%d%H%M%S)"
      echo "    Lock table '${DEFAULT_LOCK_TABLE_NAME}' status is '${describe_output}'."
      echo "    Selecting new lock table name '${LOCK_TABLE_NAME}'."
    fi

    if has_lock_table_in_state; then
      terraform state rm aws_dynamodb_table.lock >/dev/null
    fi
    return 0
  fi

  if echo "${describe_output}" | grep -qi "ResourceNotFoundException"; then
    LOCK_TABLE_NAME="${DEFAULT_LOCK_TABLE_NAME}"
    echo "    Lock table '${LOCK_TABLE_NAME}' not found in AWS. Recreating via Terraform."
    if has_lock_table_in_state; then
      terraform state rm aws_dynamodb_table.lock >/dev/null
    fi
    return 0
  fi

  echo "WARNING: Could not validate lock table status; continuing without self-heal." >&2
  echo "         AWS error: ${describe_output}" >&2
}

if ! command -v terraform >/dev/null 2>&1; then
  echo "ERROR: terraform is required in PATH." >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is required in PATH." >&2
  exit 1
fi

echo "==> Verifying AWS credentials"
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not authenticated." >&2
  echo "Run: aws configure  (or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_PROFILE)" >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "    Account: ${ACCOUNT_ID}  Region: ${AWS_REGION}"

echo "==> Running bootstrap Terraform in ${BOOTSTRAP_DIR}"
pushd "${BOOTSTRAP_DIR}" >/dev/null

terraform init -input=false

reconcile_lock_table_state

terraform apply \
  -input=false \
  -auto-approve \
  -var="aws_region=${AWS_REGION}" \
  -var="project=${TF_PROJECT}" \
  -var="environment=${ENVIRONMENT}" \
  -var="lock_table_name=${LOCK_TABLE_NAME}" \
  -var="enable_bootstrap_secrets_manager=${TF_ENABLE_BOOTSTRAP_SECRETS_MANAGER}"

STATE_BUCKET="$(terraform output -raw state_bucket_name)"
LOCK_TABLE="$(terraform output -raw lock_table_name)"

popd >/dev/null

echo "==> Writing backend configuration to ${BACKEND_FILE}"
mkdir -p "$(dirname "${BACKEND_FILE}")"
cat >"${BACKEND_FILE}" <<EOF
# Generated by phase1-bootstrap.sh — do not edit manually.
bucket         = "${STATE_BUCKET}"
key            = "${TF_BACKEND_KEY}"
region         = "${AWS_REGION}"
use_lockfile   = true
encrypt        = true
EOF

echo ""
echo "==> Bootstrap complete."
echo "    State bucket : ${STATE_BUCKET}"
echo "    Lock table   : ${LOCK_TABLE}"
echo "    Backend file : ${BACKEND_FILE}"
echo ""
echo "==> Next step — initialise the main stack:"
echo "    cd infra/terraform/aws"
echo "    terraform init -reconfigure -backend-config=environments/${ENVIRONMENT}/backend.hcl"
echo "    terraform plan -var-file=environments/${ENVIRONMENT}/${ENVIRONMENT}.tfvars"
