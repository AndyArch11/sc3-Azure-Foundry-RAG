#!/usr/bin/env bash
# Run a one-off ECS Fargate task to ingest and publish compliance control data to OpenSearch.
#
# Usage:
#   ./ops/scripts/aws/run-controls-task.sh <framework> [options]
#
# This script:
#   - reads Terraform outputs (ecs_cluster_name, ingestion_task_definition_arn, ecs_sg_id, private_subnet_ids)
#   - executes aws ecs run-task with container overrides for ingestion.runner --mode controls
#   - optionally waits for task completion and streams CloudWatch logs
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || -z "${1:-}" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/aws/run-controls-task.sh <framework> [options]

Runs a one-off ECS Fargate task to parse and publish compliance control frameworks.

Frameworks:
  aescsf              Australian Essential Eight Compliance Safety Framework
  all                 All supported frameworks (default if not specified)
  cis_controls        CIS Controls v8 (requires source document)
  essential_eight     Australian Essential Eight
  ism                 ISM (Information Security Manual)
  nist_ai_rmf         NIST AI Risk Management Framework
  nist_csf            NIST Cybersecurity Framework 2.0
  pci_dss             PCI DSS v4.0.1 (requires source document)
  pspf                Protected Security Policy Framework (2025 release)

Options:
  --env <name>            Target environment (default: dev)
  --replace-existing      Replace existing controls in OpenSearch (default: deduplicate)
  --dry-run               Parse controls but do not publish to OpenSearch
  --no-guidance           Omit guidance text (reduce payload size)
  --skip-missing-files    Skip frameworks requiring local source files when absent
  --wait                  Wait for task completion and stream CloudWatch logs
  --cluster <name>        Override cluster name (default: read from Terraform)
  --task-def <arn>        Override task definition ARN (default: read from Terraform)
  --subnet <id>           Override subnet ID (default: first private subnet from Terraform)
  --sg <id>               Override security group ID (default: read from Terraform)

Examples:
  ./ops/scripts/aws/run-controls-task.sh aescsf --env dev --wait
  ./ops/scripts/aws/run-controls-task.sh all --replace-existing --dry-run
  ./ops/scripts/aws/run-controls-task.sh essential_eight --wait --replace-existing
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/aws"

FRAMEWORK="${1}"
shift || true

# Validate framework
case "${FRAMEWORK}" in
  aescsf|all|cis_controls|essential_eight|ism|nist_ai_rmf|nist_csf|pci_dss|pspf) ;;
  *)
    echo "ERROR: Unsupported framework '${FRAMEWORK}'." >&2
    echo "Use one of: aescsf, all, cis_controls, essential_eight, ism, nist_ai_rmf, nist_csf, pci_dss, pspf" >&2
    exit 1
    ;;
esac

# Parse options
ENVIRONMENT="dev"
REPLACE_EXISTING="false"
DRY_RUN="false"
NO_GUIDANCE="false"
SKIP_MISSING_FILES="false"
WAIT="false"
CLUSTER_OVERRIDE=""
TASK_DEF_OVERRIDE=""
SUBNET_OVERRIDE=""
SG_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENVIRONMENT="${2:-}"
      shift 2
      ;;
    --replace-existing)
      REPLACE_EXISTING="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --no-guidance)
      NO_GUIDANCE="true"
      shift
      ;;
    --skip-missing-files)
      SKIP_MISSING_FILES="true"
      shift
      ;;
    --wait)
      WAIT="true"
      shift
      ;;
    --cluster)
      CLUSTER_OVERRIDE="${2:-}"
      shift 2
      ;;
    --task-def)
      TASK_DEF_OVERRIDE="${2:-}"
      shift 2
      ;;
    --subnet)
      SUBNET_OVERRIDE="${2:-}"
      shift 2
      ;;
    --sg)
      SG_OVERRIDE="${2:-}"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Use --help for usage." >&2
      exit 1
      ;;
  esac
done

case "${ENVIRONMENT}" in
  dev|test|prod) ;;
  *)
    echo "ERROR: Unsupported environment '${ENVIRONMENT}'. Use one of: dev, test, prod." >&2
    exit 1
    ;;
esac

# ── Validate prerequisites ───────────────────────────────────────────────────

if ! command -v terraform >/dev/null 2>&1; then
  echo "ERROR: terraform is required in PATH." >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is required in PATH." >&2
  exit 1
fi

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not authenticated." >&2
  echo "Run: aws configure  (or configure an instance profile / OIDC)" >&2
  exit 1
fi

BACKEND_FILE="${TF_DIR}/environments/${ENVIRONMENT}/backend.hcl"
VAR_FILE="${TF_DIR}/environments/${ENVIRONMENT}/${ENVIRONMENT}.tfvars"

if [[ ! -f "${BACKEND_FILE}" ]]; then
  echo "ERROR: Backend config not found at ${BACKEND_FILE}." >&2
  echo "Run ./ops/scripts/aws/phase1-bootstrap.sh ${ENVIRONMENT} first." >&2
  exit 1
fi

# ── Initialise Terraform and resolve outputs ──────────────────────────────────

echo "==> Initialising Terraform (${ENVIRONMENT})"
terraform -chdir="${TF_DIR}" init \
  -reconfigure \
  -input=false \
  -backend-config="${BACKEND_FILE}" \
  >/dev/null 2>&1

echo "==> Resolving Terraform outputs"

if [[ -z "${CLUSTER_OVERRIDE}" ]]; then
  CLUSTER="$(terraform -chdir="${TF_DIR}" output -raw ecs_cluster_name 2>/dev/null || true)"
  if [[ -z "${CLUSTER}" ]]; then
    echo "ERROR: Unable to resolve ecs_cluster_name from Terraform outputs." >&2
    echo "Ensure the infrastructure has been deployed with terraform apply." >&2
    exit 1
  fi
else
  CLUSTER="${CLUSTER_OVERRIDE}"
fi

if [[ -z "${TASK_DEF_OVERRIDE}" ]]; then
  TASK_DEF_ARN="$(terraform -chdir="${TF_DIR}" output -raw ingestion_task_definition_arn 2>/dev/null || true)"
  if [[ -z "${TASK_DEF_ARN}" ]]; then
    echo "ERROR: Unable to resolve ingestion_task_definition_arn from Terraform outputs." >&2
    echo "Ensure module.app_hosting has been deployed." >&2
    exit 1
  fi
else
  TASK_DEF_ARN="${TASK_DEF_OVERRIDE}"
fi

if [[ -z "${SG_OVERRIDE}" ]]; then
  SG="$(terraform -chdir="${TF_DIR}" output -raw ecs_sg_id 2>/dev/null || true)"
  if [[ -z "${SG}" ]]; then
    echo "ERROR: Unable to resolve ecs_sg_id from Terraform outputs." >&2
    exit 1
  fi
else
  SG="${SG_OVERRIDE}"
fi

if [[ -z "${SUBNET_OVERRIDE}" ]]; then
  SUBNET="$(terraform -chdir="${TF_DIR}" output -json private_subnet_ids 2>/dev/null | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data[0] if isinstance(data, list) and len(data) > 0 else "")' || true)"
  if [[ -z "${SUBNET}" ]]; then
    echo "ERROR: Unable to resolve private_subnet_ids from Terraform outputs." >&2
    exit 1
  fi
else
  SUBNET="${SUBNET_OVERRIDE}"
fi

AWS_REGION="$(aws configure get region 2>/dev/null || echo "ap-southeast-2")"

# Disable AWS CLI pager to keep script output non-interactive.
export AWS_PAGER=""

echo "    Cluster                  : ${CLUSTER}"
echo "    Task Definition ARN      : ${TASK_DEF_ARN}"
echo "    Security Group           : ${SG}"
echo "    Subnet                   : ${SUBNET}"
echo "    Region                   : ${AWS_REGION}"

# ── Preflight: verify ingestion image exists in ECR ─────────────────────────

echo "==> Preflight check: ingestion image exists in ECR"

TASK_DEF_JSON="$(aws ecs describe-task-definition \
  --task-definition "${TASK_DEF_ARN}" \
  --region "${AWS_REGION}" \
  --query 'taskDefinition.containerDefinitions' \
  --output json 2>/dev/null || true)"

if [[ -z "${TASK_DEF_JSON}" || "${TASK_DEF_JSON}" == "null" || "${TASK_DEF_JSON}" == "[]" ]]; then
  echo "ERROR: Unable to resolve container definitions from task definition ${TASK_DEF_ARN}." >&2
  exit 1
fi

TASK_CONTAINER_NAME="$(echo "${TASK_DEF_JSON}" | python3 -c 'import json,sys
try:
    containers = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
if not isinstance(containers, list) or not containers:
    print("")
    raise SystemExit(0)
for c in containers:
    if isinstance(c, dict) and c.get("name") == "ingestion":
        print("ingestion")
        raise SystemExit(0)
first = containers[0]
print(first.get("name", "") if isinstance(first, dict) else "")
' 2>/dev/null || true)"

INGESTION_IMAGE="$(echo "${TASK_DEF_JSON}" | python3 -c 'import json,sys
target_name = sys.argv[1]
try:
    containers = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
if not isinstance(containers, list):
    print("")
    raise SystemExit(0)
for c in containers:
    if isinstance(c, dict) and c.get("name") == target_name:
        print(c.get("image", ""))
        raise SystemExit(0)
print("")
' "${TASK_CONTAINER_NAME}" 2>/dev/null || true)"

TASK_LOG_GROUP="$(echo "${TASK_DEF_JSON}" | python3 -c 'import json,sys
target_name = sys.argv[1]
try:
    containers = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
if not isinstance(containers, list):
    print("")
    raise SystemExit(0)
for c in containers:
    if isinstance(c, dict) and c.get("name") == target_name:
        log_cfg = c.get("logConfiguration") or {}
        options = log_cfg.get("options") or {}
        print(options.get("awslogs-group", ""))
        raise SystemExit(0)
print("")
' "${TASK_CONTAINER_NAME}" 2>/dev/null || true)"

TASK_LOG_STREAM_PREFIX="$(echo "${TASK_DEF_JSON}" | python3 -c 'import json,sys
target_name = sys.argv[1]
try:
  containers = json.load(sys.stdin)
except Exception:
  print("")
  raise SystemExit(0)
if not isinstance(containers, list):
  print("")
  raise SystemExit(0)
for c in containers:
  if isinstance(c, dict) and c.get("name") == target_name:
    log_cfg = c.get("logConfiguration") or {}
    options = log_cfg.get("options") or {}
    print(options.get("awslogs-stream-prefix", ""))
    raise SystemExit(0)
print("")
' "${TASK_CONTAINER_NAME}" 2>/dev/null || true)"

if [[ -z "${TASK_CONTAINER_NAME}" || -z "${INGESTION_IMAGE}" || "${INGESTION_IMAGE}" == "None" ]]; then
  echo "ERROR: Unable to resolve task container name/image from task definition ${TASK_DEF_ARN}." >&2
  exit 1
fi

if [[ "${INGESTION_IMAGE}" == *"@sha256:"* ]]; then
  echo "    Container                 : ${TASK_CONTAINER_NAME}"
  echo "    Image                     : ${INGESTION_IMAGE}"
  echo "    Check                     : digest-pinned image detected (skip tag existence check)"
else
  if [[ "${INGESTION_IMAGE}" == *":"* ]]; then
    IMAGE_REF_NO_TAG="${INGESTION_IMAGE%:*}"
    IMAGE_TAG="${INGESTION_IMAGE##*:}"
  else
    IMAGE_REF_NO_TAG="${INGESTION_IMAGE}"
    IMAGE_TAG="latest"
  fi

  ECR_REPOSITORY="${IMAGE_REF_NO_TAG#*.amazonaws.com/}"

  if [[ "${ECR_REPOSITORY}" == "${IMAGE_REF_NO_TAG}" || -z "${ECR_REPOSITORY}" ]]; then
    echo "ERROR: Unsupported ingestion image format for ECR preflight: ${INGESTION_IMAGE}" >&2
    echo "Expected ECR image format: <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>" >&2
    exit 1
  fi

  if ! aws ecr describe-images \
    --repository-name "${ECR_REPOSITORY}" \
    --image-ids imageTag="${IMAGE_TAG}" \
    --region "${AWS_REGION}" \
    >/dev/null 2>&1; then
    echo "ERROR: Preflight failed: image tag not found in ECR." >&2
    echo "  Repository : ${ECR_REPOSITORY}" >&2
    echo "  Image tag  : ${IMAGE_TAG}" >&2
    echo "  Task image : ${INGESTION_IMAGE}" >&2
    echo "" >&2
    echo "Recent tags in repository:" >&2
    aws ecr describe-images \
      --repository-name "${ECR_REPOSITORY}" \
      --region "${AWS_REGION}" \
      --query 'reverse(sort_by(imageDetails,& imagePushedAt))[:10].imageTags[]' \
      --output text 2>/dev/null | tr '\t' '\n' | sed '/^$/d' | sed 's/^/  - /' >&2 || true
    echo "" >&2
    echo "Roll out app hosting with a valid ingestion tag, for example:" >&2
    echo "  ./ops/scripts/aws/rollout-app-hosting.sh ${ENVIRONMENT} apply --ingestion-tag <tag>" >&2
    exit 1
  fi

  echo "    Container                 : ${TASK_CONTAINER_NAME}"
  echo "    Image                     : ${INGESTION_IMAGE}"
  echo "    Check                     : OK (tag exists in ECR)"
fi

# ── Build container command overrides ────────────────────────────────────────

CONTAINER_CMD=("--mode" "controls" "--controls-framework" "${FRAMEWORK}")

if [[ "${REPLACE_EXISTING}" == "true" ]]; then
  CONTAINER_CMD+=("--replace-existing")
fi

if [[ "${DRY_RUN}" == "true" ]]; then
  CONTAINER_CMD+=("--dry-run")
fi

if [[ "${NO_GUIDANCE}" == "true" ]]; then
  CONTAINER_CMD+=("--no-guidance")
fi

if [[ "${SKIP_MISSING_FILES}" == "true" ]]; then
  CONTAINER_CMD+=("--skip-missing-source-files")
fi

# ── Execute ECS run-task ─────────────────────────────────────────────────────

echo ""
echo "==> Launching ECS Fargate task"
echo "    Framework          : ${FRAMEWORK}"
echo "    Replace existing   : ${REPLACE_EXISTING}"
echo "    Dry-run mode       : ${DRY_RUN}"
echo "    Omit guidance      : ${NO_GUIDANCE}"
echo "    Skip missing files : ${SKIP_MISSING_FILES}"
echo "    Wait for completion: ${WAIT}"
echo ""

# Build the JSON command array for container overrides
CONTAINER_CMD_JSON="["
for i in "${!CONTAINER_CMD[@]}"; do
  if [[ $i -gt 0 ]]; then
    CONTAINER_CMD_JSON+=","
  fi
  CONTAINER_CMD_JSON+="\"${CONTAINER_CMD[$i]}\""
done
CONTAINER_CMD_JSON+="]"

# Construct and execute the aws ecs run-task command
if ! RUN_TASK_OUTPUT=$(aws ecs run-task \
  --cluster "${CLUSTER}" \
  --launch-type FARGATE \
  --task-definition "${TASK_DEF_ARN}" \
  --region "${AWS_REGION}" \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNET}],securityGroups=[${SG}],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"${TASK_CONTAINER_NAME}\",\"command\":${CONTAINER_CMD_JSON}}]}" \
  2>&1); then
  echo "ERROR: aws ecs run-task failed." >&2
  echo "${RUN_TASK_OUTPUT}"
  exit 1
fi

if [[ -z "${RUN_TASK_OUTPUT}" ]]; then
  echo "ERROR: aws ecs run-task produced no output." >&2
  exit 1
fi

TASK_ARN="$(echo "${RUN_TASK_OUTPUT}" | python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
tasks = data.get("tasks") or []
if tasks and isinstance(tasks[0], dict):
    print(tasks[0].get("taskArn", ""))
else:
    print("")
' 2>/dev/null || true)"

if [[ -z "${TASK_ARN}" ]]; then
  echo "ERROR: Failed to launch task (no task ARN returned)." >&2
  echo "${RUN_TASK_OUTPUT}" | python3 -m json.tool 2>/dev/null || echo "${RUN_TASK_OUTPUT}"
  exit 1
fi

TASK_ID="${TASK_ARN##*/}"
echo "✓ Task launched successfully"
echo "  Task ARN : ${TASK_ARN}"
echo "  Task ID  : ${TASK_ID}"

if [[ "${WAIT}" != "true" ]]; then
  echo ""
  echo "==> Monitor task progress in the ECS console or with:"
  echo "    aws ecs describe-tasks --cluster ${CLUSTER} --tasks ${TASK_ID} --region ${AWS_REGION}"
  echo ""
  echo "==> View task logs in CloudWatch:"
  echo "    aws logs tail /rag/${ENVIRONMENT}/ingestion --follow"
  exit 0
fi

# ── Wait for task and stream logs ────────────────────────────────────────────

echo ""
echo "==> Waiting for task to complete..."

# Poll task status
LAST_STATUS=""
DESIRED_STATUS=""
STOP_CODE=""
STOPPED_REASON=""
CONTAINER_REASON=""
ATTEMPT=0
MAX_ATTEMPTS=180  # 30 minutes with 10-second polls

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
  TASK_INFO=$(aws ecs describe-tasks \
    --cluster "${CLUSTER}" \
    --tasks "${TASK_ID}" \
    --region "${AWS_REGION}" \
    --query 'tasks[0]' \
    --output json 2>/dev/null || echo "{}")

  LAST_STATUS=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("lastStatus", ""))' 2>/dev/null || true)
  DESIRED_STATUS=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("desiredStatus", ""))' 2>/dev/null || true)
  EXIT_CODE=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); containers=data.get("containers", []); print(containers[0].get("exitCode", "")) if containers else print("")' 2>/dev/null || true)
  STOP_CODE=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("stopCode", ""))' 2>/dev/null || true)
  STOPPED_REASON=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("stoppedReason", ""))' 2>/dev/null || true)
  CONTAINER_REASON=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); containers=data.get("containers", []); print(containers[0].get("reason", "")) if containers else print("")' 2>/dev/null || true)

  if [[ -n "${LAST_STATUS}" ]]; then
    echo -ne "\r  Status: ${LAST_STATUS} (Desired: ${DESIRED_STATUS})${EXIT_CODE:+ | Exit code: ${EXIT_CODE}}     "
  fi

  if [[ "${LAST_STATUS}" == "STOPPED" ]]; then
    break
  fi

  sleep 10
  ATTEMPT=$((ATTEMPT + 1))
done

echo ""
echo ""

# Refresh one final time so stop diagnostics are populated even if the polling
# loop exited during DEPROVISIONING.
TASK_INFO=$(aws ecs describe-tasks \
  --cluster "${CLUSTER}" \
  --tasks "${TASK_ID}" \
  --region "${AWS_REGION}" \
  --query 'tasks[0]' \
  --output json 2>/dev/null || echo "{}")

LAST_STATUS=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("lastStatus", ""))' 2>/dev/null || true)
DESIRED_STATUS=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("desiredStatus", ""))' 2>/dev/null || true)
EXIT_CODE=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); containers=data.get("containers", []); print(containers[0].get("exitCode", "")) if containers else print("")' 2>/dev/null || true)
STOP_CODE=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("stopCode", ""))' 2>/dev/null || true)
STOPPED_REASON=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("stoppedReason", ""))' 2>/dev/null || true)
CONTAINER_REASON=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); containers=data.get("containers", []); print(containers[0].get("reason", "")) if containers else print("")' 2>/dev/null || true)
LOG_STREAM_NAME=$(echo "${TASK_INFO}" | python3 -c 'import json,sys; data=json.load(sys.stdin); containers=data.get("containers", []); print(containers[0].get("logStreamName", "")) if containers else print("")' 2>/dev/null || true)

if [[ -z "${LOG_STREAM_NAME}" && -n "${TASK_LOG_STREAM_PREFIX}" ]]; then
  LOG_STREAM_NAME="${TASK_LOG_STREAM_PREFIX}/${TASK_CONTAINER_NAME}/${TASK_ID}"
fi

# Fetch and display logs
if [[ -n "${TASK_LOG_GROUP}" ]]; then
  LOG_GROUP="${TASK_LOG_GROUP}"
  echo "==> Fetching logs from ${LOG_GROUP}${LOG_STREAM_NAME:+ (stream: ${LOG_STREAM_NAME})}"

  if aws logs describe-log-streams \
    --log-group-name "${LOG_GROUP}" \
    --region "${AWS_REGION}" \
    >/dev/null 2>&1; then
    if [[ -n "${LOG_STREAM_NAME}" ]]; then
      aws logs get-log-events \
        --log-group-name "${LOG_GROUP}" \
        --log-stream-name "${LOG_STREAM_NAME}" \
        --region "${AWS_REGION}" \
        --query 'events[*].message' \
        --output text \
        2>/dev/null | tail -200 || true
    else
      # Fallback when ECS does not populate a log stream name in describe-tasks.
      aws logs filter-log-events \
        --log-group-name "${LOG_GROUP}" \
        --region "${AWS_REGION}" \
        --query 'events[*].message' \
        --output text \
        2>/dev/null | tail -100 || true
    fi
  else
    echo "WARNING: Log group ${LOG_GROUP} not found."
  fi
else
  echo "WARNING: Unable to determine CloudWatch log group from task definition."
fi

echo ""
if [[ "${LAST_STATUS}" == "STOPPED" && "${EXIT_CODE}" == "0" ]]; then
  echo "✓ Task completed successfully (exit code 0)."
  exit 0
elif [[ "${LAST_STATUS}" == "STOPPED" ]]; then
  echo "✗ Task stopped with exit code: ${EXIT_CODE}"
  if [[ -n "${STOP_CODE}" ]]; then
    echo "  Stop code      : ${STOP_CODE}"
  fi
  if [[ -n "${STOPPED_REASON}" ]]; then
    echo "  Stopped reason : ${STOPPED_REASON}"
  fi
  if [[ -n "${CONTAINER_REASON}" ]]; then
    echo "  Container reason: ${CONTAINER_REASON}"
  fi
  exit 1
elif [[ $ATTEMPT -ge $MAX_ATTEMPTS ]]; then
  echo "✗ Task did not complete within timeout (30 minutes)."
  exit 1
else
  echo "! Task status: ${LAST_STATUS} (Desired: ${DESIRED_STATUS})"
  if [[ -n "${STOP_CODE}" ]]; then
    echo "  Stop code      : ${STOP_CODE}"
  fi
  if [[ -n "${STOPPED_REASON}" ]]; then
    echo "  Stopped reason : ${STOPPED_REASON}"
  fi
  if [[ -n "${CONTAINER_REASON}" ]]; then
    echo "  Container reason: ${CONTAINER_REASON}"
  fi
  exit 1
fi
