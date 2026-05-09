#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./ops/scripts/local/smoke-docker-profiles.sh

Builds the current Docker profiles and runs focused smoke checks:
  - runtime image: `ingestion.runner --help`
  - poller image: `polling_worker_main --help`
  - query-web full profile: container startup + `/health`
  - query-web cloud profile: image build only

Environment variable overrides:
  QUERY_WEB_SMOKE_PORT               Optional fixed host port for query-web smoke
  QUERY_WEB_HEALTH_TIMEOUT_SECONDS   Health wait timeout (default: 90)
  QUERY_WEB_HEALTH_POLL_SECONDS      Health poll interval (default: 3)
EOF
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
QUERY_WEB_SMOKE_PORT="${QUERY_WEB_SMOKE_PORT:-}"
QUERY_WEB_HEALTH_TIMEOUT_SECONDS="${QUERY_WEB_HEALTH_TIMEOUT_SECONDS:-90}"
QUERY_WEB_HEALTH_POLL_SECONDS="${QUERY_WEB_HEALTH_POLL_SECONDS:-3}"

RUNTIME_IMAGE_TAG="runtime-profile-smoke-local"
POLLER_IMAGE_TAG="poller-profile-smoke-local"
QUERY_WEB_FULL_IMAGE_TAG="queryweb-full-profile-smoke-local"
QUERY_WEB_CLOUD_IMAGE_TAG="queryweb-cloud-profile-smoke-local"
QUERY_WEB_CONTAINER_NAME="queryweb-profile-health-smoke"

cleanup() {
  docker rm -f "${QUERY_WEB_CONTAINER_NAME}" >/dev/null 2>&1 || true
}

trap cleanup EXIT

query_web_container_running() {
  docker inspect -f '{{.State.Running}}' "${QUERY_WEB_CONTAINER_NAME}" 2>/dev/null | grep -q '^true$'
}

query_web_container_health_ok() {
  docker exec "${QUERY_WEB_CONTAINER_NAME}" python3 - <<'PY' >/dev/null 2>&1
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as resp:
    if resp.status != 200:
        raise SystemExit(1)
PY
}

echo "[profile-smoke] Building runtime image"
docker build \
  --platform linux/amd64 \
  -f "${ROOT_DIR}/runtime/Dockerfile" \
  -t "${RUNTIME_IMAGE_TAG}" \
  "${ROOT_DIR}/runtime"

echo "[profile-smoke] Building poller image"
docker build \
  --platform linux/amd64 \
  -f "${ROOT_DIR}/runtime/Dockerfile.poller" \
  -t "${POLLER_IMAGE_TAG}" \
  "${ROOT_DIR}"

echo "[profile-smoke] Building query-web full profile image"
docker build \
  --platform linux/amd64 \
  -f "${ROOT_DIR}/query_web/Dockerfile" \
  -t "${QUERY_WEB_FULL_IMAGE_TAG}" \
  "${ROOT_DIR}"

echo "[profile-smoke] Building query-web cloud profile image"
docker build \
  --platform linux/amd64 \
  -f "${ROOT_DIR}/query_web/Dockerfile" \
  --build-arg QUERY_WEB_REQUIREMENTS_FILE=/app/query-web-requirements/service-cloud.txt \
  -t "${QUERY_WEB_CLOUD_IMAGE_TAG}" \
  "${ROOT_DIR}"

echo "[profile-smoke] Verifying runtime CLI startup"
docker run --rm "${RUNTIME_IMAGE_TAG}" --help >/dev/null

echo "[profile-smoke] Verifying poller CLI startup"
docker run --rm --entrypoint python "${POLLER_IMAGE_TAG}" \
  -m runtime.assessment_orchestration.polling_worker_main --help >/dev/null

echo "[profile-smoke] Starting query-web full profile container"
docker rm -f "${QUERY_WEB_CONTAINER_NAME}" >/dev/null 2>&1 || true
DOCKER_PORT_ARGS=("127.0.0.1::8080")
if [[ -n "${QUERY_WEB_SMOKE_PORT}" ]]; then
  DOCKER_PORT_ARGS=("127.0.0.1:${QUERY_WEB_SMOKE_PORT}:8080")
fi
docker run -d \
  --name "${QUERY_WEB_CONTAINER_NAME}" \
  -p "${DOCKER_PORT_ARGS[0]}" \
  -e CLOUD_PROVIDER=local \
  -e LOCAL_VECTOR_BACKEND=inmemory \
  -e PRECEDENCE_POLICY_PATH=/app/policies/precedence_policy.json \
  -e OLLAMA_MODEL=gemma3:27b \
  -e OLLAMA_EMBEDDING_MODEL=nomic-embed-text \
  "${QUERY_WEB_FULL_IMAGE_TAG}" >/dev/null

if [[ -z "${QUERY_WEB_SMOKE_PORT}" ]]; then
  QUERY_WEB_SMOKE_PORT="$(docker port "${QUERY_WEB_CONTAINER_NAME}" 8080/tcp | awk -F: 'NR==1 {print $NF}')"
fi

echo "[profile-smoke] Waiting for query-web health on port ${QUERY_WEB_SMOKE_PORT}"
QUERY_WEB_HEALTH_TRANSPORT="host"
start_ts="$(date +%s)"
while true; do
  if [[ "${QUERY_WEB_HEALTH_TRANSPORT}" == "host" ]]; then
    if python3 - <<'PY' "${QUERY_WEB_SMOKE_PORT}" >/dev/null 2>&1
import sys
import urllib.request

port = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
    if resp.status != 200:
        raise SystemExit(1)
PY
    then
      break
    fi
  else
    if query_web_container_health_ok; then
      break
    fi
  fi

  now_ts="$(date +%s)"
  elapsed="$(( now_ts - start_ts ))"
  if [[ "${QUERY_WEB_HEALTH_TRANSPORT}" == "host" ]] && query_web_container_running; then
    echo "[profile-smoke] Host health probe failed; falling back to in-container /health probe"
    QUERY_WEB_HEALTH_TRANSPORT="docker"
    continue
  fi
  if [[ "${elapsed}" -ge "${QUERY_WEB_HEALTH_TIMEOUT_SECONDS}" ]]; then
    echo "[profile-smoke] ERROR: query-web did not become healthy within ${QUERY_WEB_HEALTH_TIMEOUT_SECONDS}s" >&2
    docker logs "${QUERY_WEB_CONTAINER_NAME}" >&2 || true
    exit 1
  fi
  sleep "${QUERY_WEB_HEALTH_POLL_SECONDS}"
done

echo "[profile-smoke] Verifying query-web health payload"
if [[ "${QUERY_WEB_HEALTH_TRANSPORT}" == "host" ]]; then
  python3 - <<'PY' "${QUERY_WEB_SMOKE_PORT}"
import json
import sys
import urllib.request

port = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
    payload = json.loads(resp.read().decode("utf-8"))

if payload.get("status") != "ok":
    raise SystemExit("health status was not ok")
if payload.get("service") != "rag-query-web":
    raise SystemExit("health service name mismatch")
PY
else
  docker exec "${QUERY_WEB_CONTAINER_NAME}" python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5) as resp:
  payload = json.loads(resp.read().decode("utf-8"))

if payload.get("status") != "ok":
  raise SystemExit("health status was not ok")
if payload.get("service") != "rag-query-web":
  raise SystemExit("health service name mismatch")
PY
fi

echo "[profile-smoke] PASS: Docker profile smoke checks succeeded"