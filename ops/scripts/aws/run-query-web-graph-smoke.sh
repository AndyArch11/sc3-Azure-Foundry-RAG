#!/usr/bin/env bash
set -euo pipefail

# Run AWS graph snapshot smoke checks for query web.
# Supports optional build+publish verification followed by AWS-backed export/node/related reads.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT_DIR}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/aws/run-query-web-graph-smoke.sh <base_url> [auth_token]

Examples:
  QUERY_GRAPH_NODE_ID="a:nist-csf-gv-gv-oc-01" \
    ./ops/scripts/aws/run-query-web-graph-smoke.sh "https://<fqdn>" "<token>"

  QUERY_GRAPH_BUILD_PAYLOAD_FILE=./ops/scripts/aws/graph-smoke-payload.sample.json \
  QUERY_GRAPH_NODE_ID="a:nist-csf-gv-gv-oc-01" \
    ./ops/scripts/aws/run-query-web-graph-smoke.sh "https://<fqdn>" "<token>"

  QUERY_GRAPH_PREFLIGHT_ONLY=true \
  QUERY_GRAPH_BUILD_PAYLOAD_FILE=./ops/scripts/aws/graph-smoke-payload.sample.json \
  QUERY_GRAPH_NODE_ID="a:nist-csf-gv-gv-oc-01" \
    ./ops/scripts/aws/run-query-web-graph-smoke.sh

Required env vars:
  QUERY_GRAPH_NODE_ID                      Node ID used for node/related verification.

Optional env vars:
  QUERY_GRAPH_BUILD_PAYLOAD_FILE           JSON file used for POST /api/graph/build.
  QUERY_GRAPH_SKIP_BUILD=true|false        Default: false. Skip build/publish validation.
  QUERY_GRAPH_PREFLIGHT_ONLY=true|false    Default: false. Validate payload locally and exit.
  QUERY_GRAPH_DEPTH                        Default: 2.
  QUERY_GRAPH_TIMEOUT_S                    Default: 30.
  QUERY_GRAPH_EXPECT_PUBLISHED=true|false  Default: true when build runs.
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

: "${QUERY_GRAPH_NODE_ID:?QUERY_GRAPH_NODE_ID is required.}"

QUERY_GRAPH_TIMEOUT_S="${QUERY_GRAPH_TIMEOUT_S:-30}"
QUERY_GRAPH_SKIP_BUILD="${QUERY_GRAPH_SKIP_BUILD:-false}"
QUERY_GRAPH_PREFLIGHT_ONLY="${QUERY_GRAPH_PREFLIGHT_ONLY:-false}"
QUERY_GRAPH_DEPTH="${QUERY_GRAPH_DEPTH:-2}"
QUERY_GRAPH_EXPECT_PUBLISHED="${QUERY_GRAPH_EXPECT_PUBLISHED:-}"
QUERY_GRAPH_BUILD_PAYLOAD_FILE="${QUERY_GRAPH_BUILD_PAYLOAD_FILE:-}"
AUTH_TOKEN="${QUERY_WEB_AUTH_TOKEN:-}"

if [[ "${QUERY_GRAPH_PREFLIGHT_ONLY,,}" != "true" && "${QUERY_GRAPH_PREFLIGHT_ONLY}" != "1" ]]; then
  : "${QUERY_WEB_BASE_URL:?QUERY_WEB_BASE_URL is required. Pass arg1 or set env var.}"
fi

BASE_URL="${QUERY_WEB_BASE_URL:-}"
BASE_URL="${BASE_URL%/}"

if [[ -z "${QUERY_GRAPH_EXPECT_PUBLISHED}" ]]; then
  if [[ "${QUERY_GRAPH_SKIP_BUILD,,}" == "true" || "${QUERY_GRAPH_SKIP_BUILD}" == "1" ]]; then
    QUERY_GRAPH_EXPECT_PUBLISHED="false"
  else
    QUERY_GRAPH_EXPECT_PUBLISHED="true"
  fi
fi

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "Missing required file: ${path}" >&2
    exit 2
  fi
}

resolve_python_cmd() {
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    echo "${ROOT_DIR}/.venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "Python interpreter not found." >&2
  exit 4
}

PYTHON_CMD="$(resolve_python_cmd)"

json_assert() {
  local file_path="$1"
  local code="$2"
  "${PYTHON_CMD}" - "$file_path" "$code" <<'PY'
import json
import sys

path = sys.argv[1]
code = sys.argv[2]
with open(path, 'r', encoding='utf-8') as handle:
    payload = json.load(handle)
namespace = {"payload": payload}
if not eval(code, {}, namespace):
    raise SystemExit(1)
PY
}

validate_build_payload() {
  local file_path="$1"
  "${PYTHON_CMD}" - "$file_path" <<'PY'
import json
import sys

from query_web.endpoints.graph import GraphBuildRequest

path = sys.argv[1]
with open(path, 'r', encoding='utf-8') as handle:
    payload = json.load(handle)
GraphBuildRequest.model_validate(payload)
print("Payload contract validation passed.")
PY
}

if [[ "${QUERY_GRAPH_SKIP_BUILD,,}" != "true" && "${QUERY_GRAPH_SKIP_BUILD}" != "1" ]]; then
  : "${QUERY_GRAPH_BUILD_PAYLOAD_FILE:?QUERY_GRAPH_BUILD_PAYLOAD_FILE is required unless QUERY_GRAPH_SKIP_BUILD=true.}"
  require_file "${QUERY_GRAPH_BUILD_PAYLOAD_FILE}"

  echo "== Preflight: build payload contract"
  validate_build_payload "${QUERY_GRAPH_BUILD_PAYLOAD_FILE}"
fi

if [[ "${QUERY_GRAPH_PREFLIGHT_ONLY,,}" == "true" || "${QUERY_GRAPH_PREFLIGHT_ONLY}" == "1" ]]; then
  echo "Preflight-only mode enabled; skipping network calls."
  exit 0
fi

echo "Running query web graph smoke against: ${BASE_URL}"
echo "  node_id=${QUERY_GRAPH_NODE_ID}"
echo "  skip_build=${QUERY_GRAPH_SKIP_BUILD}"
echo "  depth=${QUERY_GRAPH_DEPTH}"

echo "== Preflight: /health"
HEALTH_PAYLOAD="$(curl -sS -m "${QUERY_GRAPH_TIMEOUT_S}" "${BASE_URL}/health")"
echo "${HEALTH_PAYLOAD}"
if ! grep -q '"status"' <<<"${HEALTH_PAYLOAD}"; then
  echo "Health check did not return expected JSON shape." >&2
  exit 3
fi

BUILD_RESPONSE_FILE="$(mktemp)"
EXPORT_RESPONSE_FILE="$(mktemp)"
NODE_RESPONSE_FILE="$(mktemp)"
RELATED_RESPONSE_FILE="$(mktemp)"
trap 'rm -f "${BUILD_RESPONSE_FILE}" "${EXPORT_RESPONSE_FILE}" "${NODE_RESPONSE_FILE}" "${RELATED_RESPONSE_FILE}"' EXIT

if [[ "${QUERY_GRAPH_SKIP_BUILD,,}" != "true" && "${QUERY_GRAPH_SKIP_BUILD}" != "1" ]]; then
  echo "== Build: /api/graph/build"
  curl -sS -m "${QUERY_GRAPH_TIMEOUT_S}" \
    -X POST "${BASE_URL}/api/graph/build" \
    -H 'Content-Type: application/json' \
    --data-binary "@${QUERY_GRAPH_BUILD_PAYLOAD_FILE}" \
    > "${BUILD_RESPONSE_FILE}"
  cat "${BUILD_RESPONSE_FILE}"
  echo

  json_assert "${BUILD_RESPONSE_FILE}" 'payload.get("status") == "ok"'
  if [[ "${QUERY_GRAPH_EXPECT_PUBLISHED,,}" == "true" || "${QUERY_GRAPH_EXPECT_PUBLISHED}" == "1" ]]; then
    json_assert "${BUILD_RESPONSE_FILE}" 'payload.get("report", {}).get("published_snapshot") is not None'
  fi
fi

echo "== Export: /api/graph/export"
curl -sS -m "${QUERY_GRAPH_TIMEOUT_S}" \
  "${BASE_URL}/api/graph/export?auth_token=${AUTH_TOKEN}&format=json" \
  > "${EXPORT_RESPONSE_FILE}"
cat "${EXPORT_RESPONSE_FILE}"
echo
json_assert "${EXPORT_RESPONSE_FILE}" 'isinstance(payload.get("nodes"), list) and isinstance(payload.get("edges"), list)'
json_assert "${EXPORT_RESPONSE_FILE}" 'payload.get("audit", {}).get("operation") == "export"'

echo "== Node: /api/graph/nodes/{id}"
curl -sS -m "${QUERY_GRAPH_TIMEOUT_S}" \
  "${BASE_URL}/api/graph/nodes/${QUERY_GRAPH_NODE_ID}?auth_token=${AUTH_TOKEN}" \
  > "${NODE_RESPONSE_FILE}"
cat "${NODE_RESPONSE_FILE}"
echo
json_assert "${NODE_RESPONSE_FILE}" 'payload.get("node_id") == "'"${QUERY_GRAPH_NODE_ID}"'"'
json_assert "${NODE_RESPONSE_FILE}" 'payload.get("audit", {}).get("operation") == "node_get"'

echo "== Related: /api/graph/related"
curl -sS -m "${QUERY_GRAPH_TIMEOUT_S}" \
  "${BASE_URL}/api/graph/related?auth_token=${AUTH_TOKEN}&node_id=${QUERY_GRAPH_NODE_ID}&depth=${QUERY_GRAPH_DEPTH}" \
  > "${RELATED_RESPONSE_FILE}"
cat "${RELATED_RESPONSE_FILE}"
echo
json_assert "${RELATED_RESPONSE_FILE}" 'isinstance(payload.get("nodes"), list) and isinstance(payload.get("edges"), list)'
json_assert "${RELATED_RESPONSE_FILE}" 'payload.get("audit", {}).get("operation") == "related"'

echo "Graph smoke checks passed."
