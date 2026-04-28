#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${QUERY_WEB_BASE_URL:-http://localhost:8080}}"
AUTH_TOKEN="${QUERY_WEB_AUTH_TOKEN:-}"
TIMEOUT_SECONDS="${QUERY_WEB_TIMEOUT_SECONDS:-120}"
POLL_INTERVAL_SECONDS="${QUERY_WEB_POLL_INTERVAL_SECONDS:-3}"

if [[ "${BASE_URL}" == */ ]]; then
  BASE_URL="${BASE_URL%/}"
fi

echo "[smoke-local-ask] Waiting for service health at ${BASE_URL}/health"
start_ts="$(date +%s)"
while true; do
  if curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
    break
  fi

  now_ts="$(date +%s)"
  elapsed="$(( now_ts - start_ts ))"
  if [[ "${elapsed}" -ge "${TIMEOUT_SECONDS}" ]]; then
    echo "[smoke-local-ask] ERROR: query-web did not become healthy within ${TIMEOUT_SECONDS}s" >&2
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

response_json="$(curl -fsS \
  -X POST "${BASE_URL}/api/ask" \
  -H "Content-Type: application/json" \
  -d "${request_json}")"

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
