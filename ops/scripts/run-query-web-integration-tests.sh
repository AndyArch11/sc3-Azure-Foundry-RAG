#!/usr/bin/env bash
set -euo pipefail

# Run integration smoke tests for query_web from a private-network location.
# Usage:
#   ./ops/scripts/run-query-web-integration-tests.sh <base_url> [auth_token] [pytest args...]
# Or via env:
#   QUERY_WEB_BASE_URL=... QUERY_WEB_AUTH_TOKEN=... ./ops/scripts/run-query-web-integration-tests.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  run-query-web-integration-tests.sh <base_url> [auth_token] [pytest args...]

Examples:
  ./ops/scripts/run-query-web-integration-tests.sh https://<fqdn>
  ./ops/scripts/run-query-web-integration-tests.sh https://<fqdn> <token> -k lifecycle
  QUERY_WEB_PREFLIGHT_ONLY=true ./ops/scripts/run-query-web-integration-tests.sh https://<fqdn>

Env flags:
  QUERY_WEB_PREFLIGHT=true|false        # default: true
  QUERY_WEB_PREFLIGHT_ONLY=true|false   # default: false
EOF
  exit 0
fi

if [[ $# -ge 1 ]]; then
  export QUERY_WEB_BASE_URL="$1"
  shift
fi
if [[ $# -ge 1 && "${1}" != -* ]]; then
  export QUERY_WEB_AUTH_TOKEN="$1"
  shift
fi

: "${QUERY_WEB_BASE_URL:?QUERY_WEB_BASE_URL is required. Pass arg1 or set env var.}"

export QUERY_WEB_TIMEOUT_S="${QUERY_WEB_TIMEOUT_S:-30}"
export QUERY_WEB_RUN_API_ASK="${QUERY_WEB_RUN_API_ASK:-false}"
export QUERY_WEB_PREFLIGHT="${QUERY_WEB_PREFLIGHT:-true}"
export QUERY_WEB_PREFLIGHT_ONLY="${QUERY_WEB_PREFLIGHT_ONLY:-false}"

PYTEST_ARGS=("$@")

if [[ -x "${ROOT_DIR}/runtime/.venv/bin/pytest" ]]; then
  PYTEST_BIN="${ROOT_DIR}/runtime/.venv/bin/pytest"
else
  PYTEST_BIN="pytest"
fi

echo "Running query_web integration tests against: ${QUERY_WEB_BASE_URL}"
echo "QUERY_WEB_RUN_API_ASK=${QUERY_WEB_RUN_API_ASK}"
echo "QUERY_WEB_PREFLIGHT=${QUERY_WEB_PREFLIGHT}"
echo "QUERY_WEB_PREFLIGHT_ONLY=${QUERY_WEB_PREFLIGHT_ONLY}"

if [[ "${QUERY_WEB_PREFLIGHT,,}" == "true" || "${QUERY_WEB_PREFLIGHT}" == "1" ]]; then
  HOST="${QUERY_WEB_BASE_URL#*://}"
  HOST="${HOST%%/*}"

  echo "== Preflight: DNS resolve ${HOST}"
  if ! getent ahosts "${HOST}" >/dev/null 2>&1; then
    echo "DNS resolution failed for ${HOST}."
    echo "If this is a private-network-only endpoint, run from jump host or in-vnet runner."
    exit 2
  fi

  echo "== Preflight: /health"
  HEALTH_PAYLOAD="$(curl -sS -m "${QUERY_WEB_TIMEOUT_S}" "${QUERY_WEB_BASE_URL%/}/health")"
  echo "${HEALTH_PAYLOAD}"
  if ! grep -q '"status"' <<<"${HEALTH_PAYLOAD}"; then
    echo "Preflight health check did not return expected JSON shape."
    exit 3
  fi
fi

if [[ "${QUERY_WEB_PREFLIGHT_ONLY,,}" == "true" || "${QUERY_WEB_PREFLIGHT_ONLY}" == "1" ]]; then
  echo "Preflight-only mode enabled; skipping pytest execution."
  exit 0
fi

"${PYTEST_BIN}" tests/integration/test_query_web_smoke.py -m "integration and private_network" -v "${PYTEST_ARGS[@]}"
