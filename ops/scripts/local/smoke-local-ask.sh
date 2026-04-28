#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${QUERY_WEB_BASE_URL:-http://localhost:8080}}"
AUTH_TOKEN="${QUERY_WEB_AUTH_TOKEN:-}"
TIMEOUT_SECONDS="${QUERY_WEB_TIMEOUT_SECONDS:-120}"
POLL_INTERVAL_SECONDS="${QUERY_WEB_POLL_INTERVAL_SECONDS:-3}"
QUERY_WEB_CONTAINER_NAME="${QUERY_WEB_CONTAINER_NAME:-rag-query-web-local}"

if [[ "${BASE_URL}" == */ ]]; then
  BASE_URL="${BASE_URL%/}"
fi

transport="host"

docker_container_running() {
  docker inspect -f '{{.State.Running}}' "${QUERY_WEB_CONTAINER_NAME}" 2>/dev/null | grep -q '^true$'
}

docker_health_ok() {
  docker exec "${QUERY_WEB_CONTAINER_NAME}" python3 - <<'PY' >/dev/null 2>&1
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as resp:
    if resp.status != 200:
        raise SystemExit(1)
PY
}

echo "[smoke-local-ask] Waiting for service health at ${BASE_URL}/health"
start_ts="$(date +%s)"
while true; do
  if [[ "${transport}" == "host" ]]; then
    if curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
      break
    fi
  else
    if docker_health_ok; then
      break
    fi
  fi

  now_ts="$(date +%s)"
  elapsed="$(( now_ts - start_ts ))"
  if [[ "${transport}" == "host" ]] && docker_container_running; then
    echo "[smoke-local-ask] Host health endpoint unreachable; falling back to in-container probe via ${QUERY_WEB_CONTAINER_NAME}"
    transport="docker"
    continue
  fi

  if [[ "${elapsed}" -ge "${TIMEOUT_SECONDS}" ]]; then
    if [[ "${transport}" == "host" ]]; then
      echo "[smoke-local-ask] ERROR: query-web did not become healthy within ${TIMEOUT_SECONDS}s at ${BASE_URL}/health" >&2
    else
      echo "[smoke-local-ask] ERROR: query-web container is running but /health failed within ${TIMEOUT_SECONDS}s" >&2
    fi
    exit 1
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
done

echo "[smoke-local-ask] Sending /api/ask request"
request_json="$(cat <<'JSON'
{
  "question": "What is the preferred security framework?",
  "retrieve_k": 5,
  "temperature": 0.2,
  "auth_token": ""
}
JSON
)"

if [[ -n "${AUTH_TOKEN}" ]]; then
  request_json="$(python3 - <<'PY' "${request_json}" "${AUTH_TOKEN}"
import json
import sys

payload = json.loads(sys.argv[1])
payload["auth_token"] = sys.argv[2]
print(json.dumps(payload))
PY
)"
fi

if [[ "${transport}" == "host" ]]; then
  response_json="$(curl -fsS \
    -X POST "${BASE_URL}/api/ask" \
    -H "Content-Type: application/json" \
    -d "${request_json}")"
else
  response_json="$(printf '%s' "${request_json}" | docker exec -i "${QUERY_WEB_CONTAINER_NAME}" python3 -c '
import sys
import urllib.request

payload = sys.stdin.read().encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8080/api/ask",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as resp:
    print(resp.read().decode("utf-8"))
')"
fi

python3 - <<'PY' "${response_json}"
import json
import sys

payload = json.loads(sys.argv[1])
error = (payload.get("error") or "").strip()
answer = (payload.get("answer") or "").strip()

if error:
    raise SystemExit(f"[smoke-local-ask] ERROR: /api/ask returned error: {error}")
if not answer:
    raise SystemExit("[smoke-local-ask] ERROR: /api/ask returned empty answer")

print("[smoke-local-ask] PASS: received non-empty answer")
PY
