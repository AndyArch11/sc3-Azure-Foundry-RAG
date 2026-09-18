#!/usr/bin/env bash
set -euo pipefail

# Run deterministic golden-set retrieval evals, with optional RAGAS quality metrics.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

BASE_URL="${QUERY_WEB_BASE_URL:-}"
if [[ -z "${BASE_URL}" ]]; then
  echo "QUERY_WEB_BASE_URL is required (for example http://127.0.0.1:8080)" >&2
  exit 2
fi

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

ENABLE_RAGAS_FLAG=""
if [[ "${ENABLE_RAGAS:-false}" == "true" ]]; then
  ENABLE_RAGAS_FLAG="--enable-ragas"
fi

"${PYTHON_BIN}" "${REPO_ROOT}/tests/evals/run_rag_golden_eval.py" \
  --base-url "${BASE_URL}" \
  --auth-token "${QUERY_WEB_AUTH_TOKEN:-}" \
  --golden-set "${REPO_ROOT}/tests/evals/rag_golden_set.json" \
  --output "${REPO_ROOT}/local_state/evals/rag_eval_latest.json" \
  ${ENABLE_RAGAS_FLAG}

echo "Golden-set eval report written to local_state/evals/rag_eval_latest.json"
