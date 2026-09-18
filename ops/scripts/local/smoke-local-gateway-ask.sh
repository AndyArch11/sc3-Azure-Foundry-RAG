#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${RAG_GATEWAY_BASE_URL:-http://localhost:${RAG_GATEWAY_HOST_PORT:-18081}}}"
BEARER_TOKEN="${GATEWAY_BEARER_TOKEN:-}"
TIMEOUT_SECONDS="${GATEWAY_TIMEOUT_SECONDS:-90}"
POLL_INTERVAL_SECONDS="${GATEWAY_POLL_INTERVAL_SECONDS:-2}"
GATEWAY_CONTAINER_NAME="${RAG_GATEWAY_CONTAINER_NAME:-rag-gateway-local}"
QUERY_WEB_CONTAINER_NAME="${QUERY_WEB_CONTAINER_NAME:-rag-query-web-local}"

if [[ "${BASE_URL}" == */ ]]; then
  BASE_URL="${BASE_URL%/}"
fi

HOST_BASE_URLS=("${BASE_URL}")
if [[ "${BASE_URL}" == *://localhost:* || "${BASE_URL}" == *://127.0.0.1:* ]]; then
  docker_host_base_url="${BASE_URL/\/\/localhost:/\/\/host.docker.internal:}"
  docker_host_base_url="${docker_host_base_url/\/\/127.0.0.1:/\/\/host.docker.internal:}"
  if [[ "${docker_host_base_url}" != "${BASE_URL}" ]]; then
    HOST_BASE_URLS+=("${docker_host_base_url}")
  fi
fi

transport="host"

docker_container_running() {
  docker inspect -f '{{.State.Running}}' "${GATEWAY_CONTAINER_NAME}" 2>/dev/null | grep -q '^true$'
}

docker_gateway_health_ok() {
  docker exec "${QUERY_WEB_CONTAINER_NAME}" python3 -c "import sys, urllib.request; resp=urllib.request.urlopen('http://rag-gateway-local:8081/gateway/health', timeout=3); sys.exit(0 if resp.status == 200 else 1)" >/dev/null 2>&1
}

echo "[smoke-local-gateway-ask] Waiting for gateway health at ${BASE_URL}/gateway/health"
start_ts="$(date +%s)"
while true; do
  if [[ "${transport}" == "host" ]]; then
    for candidate_base_url in "${HOST_BASE_URLS[@]}"; do
      if curl -fsS --connect-timeout 3 "${candidate_base_url}/gateway/health" >/dev/null 2>&1; then
        if [[ "${candidate_base_url}" != "${BASE_URL}" ]]; then
          echo "[smoke-local-gateway-ask] Host gateway unavailable at ${BASE_URL}; using ${candidate_base_url}"
          BASE_URL="${candidate_base_url}"
        fi
        break 2
      fi
    done
  else
    if docker_gateway_health_ok; then
      break
    fi
  fi

  now_ts="$(date +%s)"
  elapsed="$(( now_ts - start_ts ))"
  if [[ "${transport}" == "host" ]] && docker_container_running; then
    echo "[smoke-local-gateway-ask] Host health endpoint unreachable; falling back to in-container probe via ${GATEWAY_CONTAINER_NAME}"
    transport="docker"
    continue
  fi

  if [[ "${elapsed}" -ge "${TIMEOUT_SECONDS}" ]]; then
    if [[ "${transport}" == "host" ]]; then
      echo "[smoke-local-gateway-ask] ERROR: gateway did not become healthy within ${TIMEOUT_SECONDS}s at ${BASE_URL}/gateway/health" >&2
    else
      echo "[smoke-local-gateway-ask] ERROR: gateway container is running but /gateway/health failed within ${TIMEOUT_SECONDS}s" >&2
    fi
    exit 1
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
done

echo "[smoke-local-gateway-ask] Sending gateway /api/v1/ask request"
request_json="$(cat <<'JSON'
{
  "question": "Summarize the top control themes.",
  "retrieve_k": 5,
  "temperature": 0.2
}
JSON
)"

curl_args=(
  -fsS
  -X POST "${BASE_URL}/api/v1/ask"
  -H "Content-Type: application/json"
  -d "${request_json}"
)

if [[ -n "${BEARER_TOKEN}" ]]; then
  curl_args+=( -H "Authorization: Bearer ${BEARER_TOKEN}" )
fi

response_status=""
response_json=""

if [[ "${transport}" == "host" ]]; then
  response_raw="$(curl -sS -w $'\n__HTTP_STATUS__:%{http_code}' "${curl_args[@]}")"
  response_status="$(printf '%s\n' "${response_raw}" | tail -n 1 | sed -n 's/^__HTTP_STATUS__://p')"
  response_json="$(printf '%s\n' "${response_raw}" | sed '$d')"
else
  probe_file="/tmp/rag_gateway_probe_response.json"

  docker exec \
    -e GATEWAY_REQ_JSON="${request_json}" \
    -e GATEWAY_BEARER_TOKEN="${BEARER_TOKEN}" \
    -e GATEWAY_RESP_PATH="${probe_file}" \
    "${QUERY_WEB_CONTAINER_NAME}" \
    python3 -c "import http.client,json,os; payload=(os.environ.get('GATEWAY_REQ_JSON') or '{}'); bearer=(os.environ.get('GATEWAY_BEARER_TOKEN') or '').strip(); headers={'Content-Type':'application/json'}; headers.update({'Authorization': f'Bearer {bearer}'} if bearer else {}); conn=http.client.HTTPConnection('rag-gateway-local', 8081, timeout=120); conn.request('POST','/api/v1/ask', body=payload.encode('utf-8'), headers=headers); resp=conn.getresponse(); body=resp.read().decode('utf-8', errors='replace'); out_path=os.environ.get('GATEWAY_RESP_PATH','/tmp/rag_gateway_probe_response.json'); open(out_path,'w',encoding='utf-8').write(json.dumps({'status': resp.status, 'body': body}))" >/dev/null 2>&1 || true

  response_raw="$(docker exec "${QUERY_WEB_CONTAINER_NAME}" python3 -c "import os; p='${probe_file}'; print(open(p, encoding='utf-8').read() if os.path.exists(p) else '')" 2>/dev/null || true)"

  if [[ -z "${response_raw//[[:space:]]/}" ]]; then
    echo "[smoke-local-gateway-ask] ERROR: docker-mode gateway probe returned no output" >&2
    echo "[smoke-local-gateway-ask] Last 40 gateway logs:" >&2
    docker logs --tail 40 "${GATEWAY_CONTAINER_NAME}" >&2 || true
    echo "[smoke-local-gateway-ask] Last 40 query-web logs:" >&2
    docker logs --tail 40 "${QUERY_WEB_CONTAINER_NAME}" >&2 || true
    exit 1
  fi

  if ! python3 - <<'PY' "${response_raw}" >/dev/null 2>&1
import json
import sys

json.loads(sys.argv[1])
PY
  then
    echo "[smoke-local-gateway-ask] ERROR: docker-mode wrapper response is not JSON" >&2
    echo "[smoke-local-gateway-ask] Wrapper content follows:" >&2
    printf '%s\n' "${response_raw}" >&2
    exit 1
  fi

  response_status="$(python3 - <<'PY' "${response_raw}"
import json
import sys

payload = json.loads(sys.argv[1])
print(payload.get("status", 0))
PY
)"

  response_json="$(python3 - <<'PY' "${response_raw}"
import json
import sys

payload = json.loads(sys.argv[1])
print(payload.get("body", ""), end="")
PY
)"
fi

if [[ ! "${response_status}" =~ ^[0-9]+$ ]]; then
  echo "[smoke-local-gateway-ask] ERROR: could not parse response status" >&2
  echo "[smoke-local-gateway-ask] Raw response wrapper: ${response_raw}" >&2
  exit 1
fi

if [[ "${response_status}" -lt 200 || "${response_status}" -ge 300 ]]; then
  echo "[smoke-local-gateway-ask] ERROR: gateway returned HTTP ${response_status}" >&2
  echo "[smoke-local-gateway-ask] Raw response body follows:" >&2
  printf '%s\n' "${response_json}" >&2
  exit 1
fi

if [[ -z "${response_json//[[:space:]]/}" ]]; then
  echo "[smoke-local-gateway-ask] ERROR: gateway returned an empty response body" >&2
  echo "[smoke-local-gateway-ask] HTTP status: ${response_status}" >&2
  exit 1
fi

python3 - <<'PY' "${response_json}"
import json
import sys

raw = sys.argv[1]
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    snippet = raw[:400].replace("\n", " ")
    raise SystemExit(f"[smoke-local-gateway-ask] ERROR: non-JSON response ({exc}): {snippet}")

error = (payload.get("error") or "").strip()
answer = (payload.get("answer") or "").strip()

if error:
    raise SystemExit(f"[smoke-local-gateway-ask] ERROR: gateway /api/v1/ask returned error: {error}")
if not answer:
  raise SystemExit("[smoke-local-gateway-ask] ERROR: gateway /api/v1/ask returned empty answer")

print("[smoke-local-gateway-ask] PASS: received non-empty answer through gateway")
PY
