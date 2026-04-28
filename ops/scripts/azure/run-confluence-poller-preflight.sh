#!/usr/bin/env bash
set -euo pipefail

# Run a combined Confluence poller preflight:
# 1) one-shot smoke cycle
# 2) deployed app health/log check (optional)

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/run-confluence-poller-preflight.sh [env] [--skip-health] [--no-dry-run] [--lines <n>] [--follow]

Defaults:
  env   = dev
  lines = 100

Behaviour:
  - Runs run-confluence-poller-smoke.sh first.
  - Then runs check-confluence-poller-health.sh unless --skip-health is supplied.

Examples:
  ./ops/scripts/run-confluence-poller-preflight.sh
  ./ops/scripts/run-confluence-poller-preflight.sh dev --lines 200
  ./ops/scripts/run-confluence-poller-preflight.sh dev --skip-health
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_SCRIPT="${ROOT_DIR}/ops/scripts/run-confluence-poller-smoke.sh"
HEALTH_SCRIPT="${ROOT_DIR}/ops/scripts/check-confluence-poller-health.sh"

ENVIRONMENT="${1:-dev}"
if [[ $# -gt 0 && "${1}" != --* ]]; then
  shift
fi

SKIP_HEALTH="false"
NO_DRY_RUN="false"
TAIL_LINES="100"
FOLLOW_LOGS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-health)
      SKIP_HEALTH="true"
      shift
      ;;
    --no-dry-run)
      NO_DRY_RUN="true"
      shift
      ;;
    --lines)
      TAIL_LINES="${2:-100}"
      shift 2
      ;;
    --follow)
      FOLLOW_LOGS="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Use --help for usage."
      exit 1
      ;;
  esac
done

if [[ ! -x "${SMOKE_SCRIPT}" ]]; then
  echo "Missing smoke script: ${SMOKE_SCRIPT}" >&2
  exit 2
fi

if [[ ! -x "${HEALTH_SCRIPT}" ]]; then
  echo "Missing health script: ${HEALTH_SCRIPT}" >&2
  exit 2
fi

echo "==> Step 1/2: Running one-shot poller smoke"
SMOKE_ARGS=()
if [[ "${NO_DRY_RUN}" == "true" ]]; then
  SMOKE_ARGS+=(--no-dry-run)
fi
"${SMOKE_SCRIPT}" "${SMOKE_ARGS[@]}"

echo "==> Smoke step complete"

if [[ "${SKIP_HEALTH}" == "true" ]]; then
  echo "==> Step 2/2 skipped (--skip-health)"
  exit 0
fi

echo "==> Step 2/2: Checking deployed poller health and logs"
HEALTH_ARGS=("${ENVIRONMENT}" --lines "${TAIL_LINES}")
if [[ "${FOLLOW_LOGS}" == "true" ]]; then
  HEALTH_ARGS+=(--follow)
fi

"${HEALTH_SCRIPT}" "${HEALTH_ARGS[@]}"

echo "==> Preflight complete"
