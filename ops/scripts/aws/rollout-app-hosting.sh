#!/usr/bin/env bash
# Deploy or update the ECS app_hosting module (query-web service, confluence poller, and ingestion task definition).
#
# Usage:
#   ./ops/scripts/aws/rollout-app-hosting.sh <env> [plan|apply] [options]
#
# This script:
#   - targets module.app_hosting and module.app_secrets only
#   - optionally overrides query-web, ingestion, and confluence-poller image tags at apply time
#   - optionally enables or disables the confluence poller service
#   - can update the confluence_api_token field in Secrets Manager for existing environments
#   - waits for ECS service stability after apply
#   - does NOT touch data services, networking, or identity modules
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/aws/rollout-app-hosting.sh <env> [plan|apply] [options]

Runs a targeted Terraform plan/apply for module.app_hosting and module.app_secrets only.

Defaults:
  env    = dev
  action = apply

Options:
  --query-web-tag <tag>            Override query_web_image_tag at apply time
  --ingestion-tag <tag>            Override ingestion_image_tag at apply time
  --confluence-poller-tag <tag>    Override confluence_poller_image_tag at apply time
  --enable-confluence-poller       Set enable_confluence_poller_service=true
  --disable-confluence-poller      Set enable_confluence_poller_service=false
  --confluence-base-url <url>      Override confluence_base_url at apply time
  --confluence-auth-mode <mode>    Override confluence_auth_mode at apply time
  --confluence-auth-email <email>  Override confluence_auth_email at apply time
  --confluence-api-token <token>   Update the confluence_api_token secret field for the app secret
  --confluence-cloud-id <id>       Override confluence_cloud_id at apply time
  --confluence-account-id <id>     Override confluence_account_id at apply time
  --confluence-space-keys <csv>    Override confluence_poll_space_keys at apply time
  --confluence-poll-dry-run <bool> Override confluence_poll_dry_run at apply time
  --no-wait                        Skip waiting for ECS service stabilisation after apply

Examples:
  ./ops/scripts/aws/rollout-app-hosting.sh dev apply --query-web-tag 202604201200-abc1234
  ./ops/scripts/aws/rollout-app-hosting.sh dev apply --confluence-poller-tag 202605061200-abc1234 --enable-confluence-poller
  ./ops/scripts/aws/rollout-app-hosting.sh dev apply --enable-confluence-poller --confluence-base-url https://myorg.atlassian.net --confluence-auth-email svc@myorg.com --confluence-api-token '<token>'
  ./ops/scripts/aws/rollout-app-hosting.sh prod plan --query-web-tag 202604201200-abc1234
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/aws"

ENVIRONMENT="${1:-dev}"
ACTION="${2:-apply}"

shift $(( $# >= 1 ? 1 : 0 ))
shift $(( $# >= 1 ? 1 : 0 ))

case "${ENVIRONMENT}" in
  dev|test|prod) ;;
  *)
    echo "Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod." >&2
    exit 1
    ;;
esac

case "${ACTION}" in
  plan|apply) ;;
  *)
    echo "Unsupported action '${ACTION}'. Use one of: plan, apply." >&2
    exit 1
    ;;
esac

QUERY_WEB_TAG=""
INGESTION_TAG=""
CONFLUENCE_POLLER_TAG=""
ENABLE_CONFLUENCE_POLLER=""
CONFLUENCE_BASE_URL=""
CONFLUENCE_AUTH_MODE=""
CONFLUENCE_AUTH_EMAIL=""
CONFLUENCE_API_TOKEN=""
CONFLUENCE_CLOUD_ID=""
CONFLUENCE_ACCOUNT_ID=""
CONFLUENCE_SPACE_KEYS=""
CONFLUENCE_POLL_DRY_RUN=""
NO_WAIT="false"
FORCE_CONFLUENCE_POLLER_REDEPLOY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query-web-tag)
      QUERY_WEB_TAG="${2:-}"
      shift 2
      ;;
    --ingestion-tag)
      INGESTION_TAG="${2:-}"
      shift 2
      ;;
    --confluence-poller-tag)
      CONFLUENCE_POLLER_TAG="${2:-}"
      shift 2
      ;;
    --enable-confluence-poller)
      ENABLE_CONFLUENCE_POLLER="true"
      shift
      ;;
    --disable-confluence-poller)
      ENABLE_CONFLUENCE_POLLER="false"
      shift
      ;;
    --confluence-base-url)
      CONFLUENCE_BASE_URL="${2:-}"
      shift 2
      ;;
    --confluence-auth-mode)
      CONFLUENCE_AUTH_MODE="${2:-}"
      shift 2
      ;;
    --confluence-auth-email)
      CONFLUENCE_AUTH_EMAIL="${2:-}"
      shift 2
      ;;
    --confluence-api-token)
      CONFLUENCE_API_TOKEN="${2:-}"
      shift 2
      ;;
    --confluence-cloud-id)
      CONFLUENCE_CLOUD_ID="${2:-}"
      shift 2
      ;;
    --confluence-account-id)
      CONFLUENCE_ACCOUNT_ID="${2:-}"
      shift 2
      ;;
    --confluence-space-keys)
      CONFLUENCE_SPACE_KEYS="${2:-}"
      shift 2
      ;;
    --confluence-poll-dry-run)
      CONFLUENCE_POLL_DRY_RUN="${2:-}"
      shift 2
      ;;
    --no-wait)
      NO_WAIT="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Use --help for usage." >&2
      exit 1
      ;;
  esac
done

BACKEND_FILE="${TF_DIR}/environments/${ENVIRONMENT}/backend.hcl"
VAR_FILE="${TF_DIR}/environments/${ENVIRONMENT}/${ENVIRONMENT}.tfvars"

if ! command -v terraform >/dev/null 2>&1; then
  echo "ERROR: terraform is required in PATH." >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is required in PATH." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required in PATH for secret updates." >&2
  exit 1
fi

# Ensure Terraform's AWS SDK also loads shared config (required for many
# AWS SSO/profile-based auth flows).
export AWS_SDK_LOAD_CONFIG="${AWS_SDK_LOAD_CONFIG:-1}"

# If the operator authenticated with `aws login` but has not exported
# AWS_ACCESS_KEY_ID/AWS_SESSION_TOKEN into the shell, Terraform may fail with
# "No valid credential sources found" while `aws sts get-caller-identity`
# still works. Bootstrap SDK-compatible env vars from AWS CLI when possible.
if [[ -z "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_PROFILE:-}" ]]; then
  if exported_creds="$(aws configure export-credentials --format env 2>/dev/null)"; then
    # shellcheck disable=SC1090
    eval "${exported_creds}"
  fi
fi

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not authenticated." >&2
  echo "Run: aws configure  (or configure an instance profile / OIDC)" >&2
  exit 1
fi

if [[ -z "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_PROFILE:-}" ]]; then
  echo "ERROR: AWS credentials are not available to Terraform provider." >&2
  echo "Set AWS_PROFILE or export AWS credentials before rollout." >&2
  echo "For AWS CLI login sessions, run: eval \"\$(aws configure export-credentials --format env)\"" >&2
  exit 1
fi

if [[ ! -f "${BACKEND_FILE}" ]]; then
  echo "ERROR: Backend config not found at ${BACKEND_FILE}." >&2
  echo "Run ./ops/scripts/aws/phase1-bootstrap.sh ${ENVIRONMENT} first." >&2
  exit 1
fi

_read_tfvars_value() {
  local key="$1"
  local file="$2"
  [[ -f "${file}" ]] || return 1
  sed -nE "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"([^\"]+)\"[[:space:]]*$/\1/p" "${file}" | head -n 1
}

_resolve_app_secret_name() {
  local project aws_region_short
  project="${TF_PROJECT:-$(_read_tfvars_value project "${VAR_FILE}" || true)}"
  project="${project:-rag}"
  aws_region_short="${AWS_REGION_SHORT:-$(_read_tfvars_value aws_region_short "${VAR_FILE}" || true)}"
  if [[ -z "${aws_region_short}" ]]; then
    echo "ERROR: Could not determine aws_region_short from ${VAR_FILE}." >&2
    return 1
  fi
  printf 'app/%s-%s-%s\n' "${project}" "${ENVIRONMENT}" "${aws_region_short}"
}

_update_confluence_secret() {
  local secret_name current_secret updated_secret
  secret_name="$(_resolve_app_secret_name)"
  current_secret="$(aws secretsmanager get-secret-value --secret-id "${secret_name}" --query 'SecretString' --output text 2>/dev/null || echo '{}')"
  updated_secret="$(python3 - "${current_secret}" "${CONFLUENCE_API_TOKEN}" <<'PY'
import json
import sys

raw = sys.argv[1]
token = sys.argv[2]
if not raw or raw == 'None':
    payload = {}
else:
    payload = json.loads(raw)
payload['confluence_api_token'] = token
print(json.dumps(payload))
PY
)"
  aws secretsmanager put-secret-value --secret-id "${secret_name}" --secret-string "${updated_secret}" >/dev/null
}

echo "==> Initialising Terraform (${ENVIRONMENT})"
terraform -chdir="${TF_DIR}" init \
  -reconfigure \
  -input=false \
  -backend-config="${BACKEND_FILE}"

EXTRA_VAR_ARGS=()
LATE_OVERRIDE_ARGS=()

if [[ -n "${QUERY_WEB_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=query_web_image_tag=${QUERY_WEB_TAG}")
fi
if [[ -n "${INGESTION_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=ingestion_image_tag=${INGESTION_TAG}")
fi
if [[ -n "${CONFLUENCE_POLLER_TAG}" ]]; then
  EXTRA_VAR_ARGS+=("-var=confluence_poller_image_tag=${CONFLUENCE_POLLER_TAG}")
fi
if [[ -n "${ENABLE_CONFLUENCE_POLLER}" ]]; then
  EXTRA_VAR_ARGS+=("-var=enable_confluence_poller_service=${ENABLE_CONFLUENCE_POLLER}")
fi
if [[ -n "${CONFLUENCE_BASE_URL}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_base_url=${CONFLUENCE_BASE_URL}")
fi
if [[ -n "${CONFLUENCE_AUTH_MODE}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_auth_mode=${CONFLUENCE_AUTH_MODE}")
fi
if [[ -n "${CONFLUENCE_AUTH_EMAIL}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_auth_email=${CONFLUENCE_AUTH_EMAIL}")
fi
if [[ -n "${CONFLUENCE_CLOUD_ID}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_cloud_id=${CONFLUENCE_CLOUD_ID}")
fi
if [[ -n "${CONFLUENCE_ACCOUNT_ID}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_account_id=${CONFLUENCE_ACCOUNT_ID}")
fi
if [[ -n "${CONFLUENCE_SPACE_KEYS}" ]]; then
  IFS=',' read -r -a _key_arr <<< "${CONFLUENCE_SPACE_KEYS}"
  _hcl_keys=""
  for _key in "${_key_arr[@]}"; do
    _trimmed="$(echo "${_key}" | xargs)"
    [[ -z "${_trimmed}" ]] && continue
    _hcl_keys+="\"${_trimmed}\"," 
  done
  if [[ -n "${_hcl_keys}" ]]; then
    LATE_OVERRIDE_ARGS+=("-var=confluence_poll_space_keys=[${_hcl_keys%,}]")
  fi
fi
if [[ -n "${CONFLUENCE_POLL_DRY_RUN}" ]]; then
  LATE_OVERRIDE_ARGS+=("-var=confluence_poll_dry_run=${CONFLUENCE_POLL_DRY_RUN}")
fi

if [[ -n "${CONFLUENCE_API_TOKEN}" ]]; then
  if [[ "${ACTION}" == "plan" ]]; then
    echo "INFO: --confluence-api-token is ignored during plan."
  else
    SECRET_NAME="$(_resolve_app_secret_name)"
    if aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" >/dev/null 2>&1; then
      echo "==> Updating Confluence API token in Secrets Manager (${SECRET_NAME})"
      _update_confluence_secret
      FORCE_CONFLUENCE_POLLER_REDEPLOY="true"
    else
      EXTRA_VAR_ARGS+=("-var=initial_confluence_api_token=${CONFLUENCE_API_TOKEN}")
    fi
  fi
fi

echo "==> Running ${ACTION} (module.app_hosting, module.app_secrets)"

TF_ACTION_ARGS=(
  -input=false
  -var-file="${VAR_FILE}"
)

if [[ "${ACTION}" == "plan" ]]; then
  TF_ACTION_ARGS+=(-refresh=false)
fi

TF_ACTION_ARGS+=(
  "${EXTRA_VAR_ARGS[@]+"${EXTRA_VAR_ARGS[@]}"}"
  -target=module.app_hosting
  -target=module.app_secrets
  "${LATE_OVERRIDE_ARGS[@]+"${LATE_OVERRIDE_ARGS[@]}"}"
)

if [[ "${ACTION}" == "apply" ]]; then
  TF_ACTION_ARGS+=(-auto-approve)
fi

terraform -chdir="${TF_DIR}" "${ACTION}" "${TF_ACTION_ARGS[@]}"

if [[ "${ACTION}" != "apply" ]]; then
  exit 0
fi

AWS_REGION="$(aws configure get region 2>/dev/null || echo "ap-southeast-2")"
CLUSTER_NAME="$(terraform -chdir="${TF_DIR}" output -raw ecs_cluster_name 2>/dev/null || true)"
QUERY_WEB_SERVICE_NAME="$(terraform -chdir="${TF_DIR}" output -raw query_web_service_name 2>/dev/null || true)"
CONFLUENCE_POLLER_SERVICE_NAME="$(terraform -chdir="${TF_DIR}" output -raw confluence_poller_service_name 2>/dev/null || true)"

if [[ "${FORCE_CONFLUENCE_POLLER_REDEPLOY}" == "true" && -n "${CLUSTER_NAME}" && -n "${CONFLUENCE_POLLER_SERVICE_NAME}" ]]; then
  echo "==> Forcing new deployment for Confluence poller service to pick up the updated secret"
  aws ecs update-service \
    --region "${AWS_REGION}" \
    --cluster "${CLUSTER_NAME}" \
    --service "${CONFLUENCE_POLLER_SERVICE_NAME}" \
    --force-new-deployment >/dev/null
fi

if [[ "${NO_WAIT}" == "true" ]]; then
  echo "==> --no-wait set; skipping ECS stabilisation check."
  exit 0
fi

if [[ -z "${CLUSTER_NAME}" ]]; then
  echo "INFO: ECS cluster output unavailable; skipping ECS wait."
  exit 0
fi

_wait_for_service() {
  local service_name="$1"
  local label="$2"
  [[ -n "${service_name}" ]] || return 0

  echo "==> Waiting for ${label} service to stabilise"
  echo "    Cluster: ${CLUSTER_NAME}"
  echo "    Service: ${service_name}"

  if aws ecs wait services-stable \
    --region "${AWS_REGION}" \
    --cluster "${CLUSTER_NAME}" \
    --services "${service_name}"; then
    echo "==> ${label} service is stable."
  else
    echo "WARNING: 'aws ecs wait services-stable' returned non-zero for ${label}. Check the ECS console for task failure details." >&2
    echo "    aws ecs describe-services --cluster ${CLUSTER_NAME} --services ${service_name}"
  fi
}

_wait_for_service "${QUERY_WEB_SERVICE_NAME}" "query-web"
_wait_for_service "${CONFLUENCE_POLLER_SERVICE_NAME}" "confluence-poller"

echo ""
echo "==> Rollout complete."
if [[ -n "${QUERY_WEB_TAG}" ]]; then
  echo "    query-web tag         : ${QUERY_WEB_TAG}"
fi
if [[ -n "${INGESTION_TAG}" ]]; then
  echo "    ingestion tag         : ${INGESTION_TAG}"
fi
if [[ -n "${CONFLUENCE_POLLER_TAG}" ]]; then
  echo "    confluence-poller tag: ${CONFLUENCE_POLLER_TAG}"
fi
if [[ -n "${ENABLE_CONFLUENCE_POLLER}" ]]; then
  echo "    confluence poller    : ${ENABLE_CONFLUENCE_POLLER}"
fi
