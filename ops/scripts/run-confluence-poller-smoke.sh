#!/usr/bin/env bash
set -euo pipefail

# Run a one-shot dry-run cycle for the Confluence poller worker.
# Intended for jumpbox/private-network smoke verification before enabling
# continuous polling in Container Apps.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"
RUNTIME_DIR="${ROOT_DIR}/runtime"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/run-confluence-poller-smoke.sh [--no-dry-run] [-- <extra args>]

Runs assessment_orchestration.polling_worker_main --once with environment
preflight checks.

Default behavior:
  - Forces CONFLUENCE_POLL_DRY_RUN=true unless --no-dry-run is supplied.
  - Requires all core Confluence and Cosmos env vars to be set.

Required env vars:
  AZURE_COSMOS_ENDPOINT
  AZURE_COSMOS_DATABASE_NAME
  AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME
  CONFLUENCE_BASE_URL
  CONFLUENCE_AUTH_MODE
  CONFLUENCE_ACCOUNT_ID

Auth requirements:
  CONFLUENCE_AUTH_MODE=basic: requires CONFLUENCE_AUTH_EMAIL + CONFLUENCE_API_TOKEN
  CONFLUENCE_AUTH_MODE=bearer: requires CONFLUENCE_API_TOKEN (and typically CONFLUENCE_CLOUD_ID)
  CONFLUENCE_AUTH_MODE=oauth: requires oauth env vars supported by runtime wiring

Examples:
  # Safe smoke (default dry-run)
  ./ops/scripts/run-confluence-poller-smoke.sh

  # Explicitly disable dry-run (posts comments if events are detected)
  ./ops/scripts/run-confluence-poller-smoke.sh --no-dry-run

  # Pass additional worker args
  ./ops/scripts/run-confluence-poller-smoke.sh -- --once
EOF
  exit 0
fi

NO_DRY_RUN="false"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-dry-run)
      NO_DRY_RUN="true"
      shift
      ;;
    --)
      shift
      EXTRA_ARGS=("$@")
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

require_env() {
  local key="$1"
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required env var: ${key}" >&2
    exit 2
  fi
}

# Core configuration requirements
require_env AZURE_COSMOS_ENDPOINT
require_env AZURE_COSMOS_DATABASE_NAME
require_env AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME
require_env CONFLUENCE_BASE_URL
require_env CONFLUENCE_AUTH_MODE
require_env CONFLUENCE_ACCOUNT_ID

AUTH_MODE="${CONFLUENCE_AUTH_MODE,,}"
case "${AUTH_MODE}" in
  basic)
    require_env CONFLUENCE_AUTH_EMAIL
    require_env CONFLUENCE_API_TOKEN
    ;;
  bearer)
    require_env CONFLUENCE_API_TOKEN
    ;;
  oauth)
    # Detailed oauth checks are handled in runtime wiring; this keeps smoke preflight lightweight.
    ;;
  *)
    echo "Unsupported CONFLUENCE_AUTH_MODE='${CONFLUENCE_AUTH_MODE}'. Expected basic, bearer, or oauth." >&2
    exit 2
    ;;
esac

if [[ "${NO_DRY_RUN}" != "true" ]]; then
  export CONFLUENCE_POLL_DRY_RUN="true"
fi

PYTHON_CMD=()
if [[ -x "${ROOT_DIR}/runtime/.venv/bin/python" ]]; then
  PYTHON_CMD=("${ROOT_DIR}/runtime/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
else
  echo "Python interpreter not found." >&2
  exit 3
fi

echo "Running one-shot Confluence poller smoke cycle..."
echo "  dry_run=${CONFLUENCE_POLL_DRY_RUN:-false}"
echo "  auth_mode=${CONFLUENCE_AUTH_MODE}"
echo "  cosmos_db=${AZURE_COSMOS_DATABASE_NAME}"
echo "  cosmos_container=${AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME}"

cd "${RUNTIME_DIR}"
export PYTHONPATH="${RUNTIME_DIR}:${PYTHONPATH:-}"

"${PYTHON_CMD[@]}" -m assessment_orchestration.polling_worker_main --once "${EXTRA_ARGS[@]}"

echo
echo "Cosmos readback (confluence source):"
"${PYTHON_CMD[@]}" - <<'PY'
import json
import os
from datetime import UTC, datetime, timedelta

from assessment_orchestration.polling_worker import create_cosmos_state_store_from_env

store = create_cosmos_state_store_from_env(dict(os.environ))
source = "confluence"
since_iso = (datetime.now(UTC) - timedelta(hours=24)).isoformat()

state = store.load_state(source)
summary = store.get_latest_poll_run_summary(source)
pages = store.list_recent_page_assessments(source, since_iso=since_iso, limit=5)
failures = store.list_recent_failures(source, since_iso=since_iso, limit=5)

payload = {
  "state": {
    "watermark": state.watermark,
    "last_success_at": state.last_success_at,
    "last_processed_event_id": state.last_processed_event_id,
    "poll_count": state.poll_count,
    "last_error": state.last_error or {},
  },
  "summary": (
    {
      "polled_at": summary.polled_at,
      "since_iso": summary.since_iso,
      "watermark": summary.watermark,
      "mentions_found": summary.mentions_found,
      "jobs_queued": summary.jobs_queued,
      "terminal_failures": summary.terminal_failures,
      "error_message": summary.error_message,
      "space_keys": list(summary.space_keys),
    }
    if summary
    else None
  ),
  "recent_page_assessment_count_24h": len(pages),
  "recent_failure_count_24h": len(failures),
  "recent_page_assessments": [
    {
      "target_id": p.target_id,
      "title": p.title,
      "framework_scope": p.framework_scope,
      "status": p.status,
      "assessed_at": p.assessed_at,
      "findings_count": p.findings_count,
    }
    for p in pages
  ],
  "recent_failures": [
    {
      "event_id": f.event_id,
      "status": f.status,
      "attempt_count": f.attempt_count,
      "last_attempt_at": f.last_attempt_at,
      "last_error": f.last_error,
    }
    for f in failures
  ],
}

print(json.dumps(payload, ensure_ascii=True, default=str))
PY
