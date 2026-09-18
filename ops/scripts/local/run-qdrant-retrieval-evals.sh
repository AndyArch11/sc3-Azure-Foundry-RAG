#!/usr/bin/env bash
set -euo pipefail

# Run retrieval-only golden-set evals directly against Qdrant.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

INDEX_NAME="${AZURE_SEARCH_INDEX_NAME:-${SEARCH_INDEX_NAME:-grounding-index}}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

"${PYTHON_BIN}" "${REPO_ROOT}/tests/evals/run_qdrant_retrieval_eval.py" \
  --golden-set "${REPO_ROOT}/tests/evals/rag_golden_set.json" \
  --index-name "${INDEX_NAME}" \
  --qdrant-url "${QDRANT_URL}" \
  --output "${REPO_ROOT}/local_state/evals/qdrant_retrieval_eval_latest.json"

echo "Qdrant retrieval eval report written to local_state/evals/qdrant_retrieval_eval_latest.json"
