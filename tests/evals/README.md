# Evals

This folder contains evaluation assets and runners for retrieval and answer-quality checks.

## Contents

- `rag_golden_set.json`: Golden test suite used by eval runners.
- `schemas/`: JSON schemas for eval artifacts.
- `run_rag_golden_eval.py`: End-to-end eval against query-web `/api/ask`.
- `run_qdrant_retrieval_eval.py`: Retrieval-only eval directly against Qdrant.
- `skill_selection_cases.json`: Additional eval input cases.

## Golden Eval (query-web)

Runs live queries against the API and computes deterministic retrieval metrics, with optional RAGAS metrics.

```bash
python tests/evals/run_rag_golden_eval.py \
  --base-url http://host.docker.internal:18080/
```

Optional RAGAS:

```bash
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

Default output:

- `local_state/evals/rag_eval_latest.json`

### Golden Eval Environment Variables

The runner supports the following environment variables:

- `QUERY_WEB_BASE_URL`: Default for `--base-url`.
- `QUERY_WEB_AUTH_TOKEN`: Default for `--auth-token` (Bearer token).
- `QUERY_WEB_TIMEOUT_S`: Default for `--timeout-s`.
- `OPENAI_API_KEY`: Used by default RAGAS evaluator when `--enable-ragas` is set.
- `OPENAI_ADMIN_KEY`: Alternate credential fallback for default RAGAS evaluator.

RAGAS local/custom evaluator variables:

- `RAGAS_LLM_MODEL`: Enables `llm_factory` path and selects evaluator model.
- `RAGAS_LLM_BASE_URL`: Base URL for OpenAI-compatible/local evaluator endpoint.
- `RAGAS_LLM_PROVIDER`: Provider passed to `llm_factory` (default: `openai`).
- `RAGAS_LLM_API_KEY`: API key for evaluator endpoint.
- `RAGAS_LLM_ADAPTER`: Adapter passed to `llm_factory` (default: `auto`).
- `RAGAS_LLM_MAX_TOKENS`: Maximum completion budget for the evaluator model (default: `4096`).
- `RAGAS_LLM_TEMPERATURE`: Temperature for evaluator generation (default: `0.0`).

Behaviour notes:

- If `RAGAS_LLM_MODEL` is set, the custom evaluator path is used.
- If `RAGAS_LLM_BASE_URL` is set and no key is provided, the runner auto-uses `local` as API key.
- If `RAGAS_LLM_MODEL` is not set, RAGAS falls back to default OpenAI credentials (`OPENAI_API_KEY` or `OPENAI_ADMIN_KEY`).
- If you see `IncompleteOutputException`, increase `RAGAS_LLM_MAX_TOKENS` or switch to a larger model.

Example (default hosted evaluator credentials):

```bash
OPENAI_API_KEY="<key>" \
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

Example (local OpenAI-compatible evaluator):

```bash
RAGAS_LLM_MODEL="ollama_chat/gemma4:26b" \
RAGAS_LLM_BASE_URL="http://host.docker.internal:11434/v1" \
RAGAS_LLM_PROVIDER="litellm" \
RAGAS_LLM_ADAPTER="auto" \
RAGAS_LLM_API_KEY="local" \
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

### Ollama Model Presets (RTX 5090 32GB)

Recommended default (balanced quality/speed):

```bash
RAGAS_LLM_MODEL="ollama_chat/qwen2.5:7b-instruct" \
RAGAS_LLM_BASE_URL="http://host.docker.internal:11434/v1" \
RAGAS_LLM_PROVIDER="openai" \
RAGAS_LLM_API_KEY="local" \
RAGAS_LLM_ADAPTER="auto" \
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

If that still truncates on your hardware, try:

```bash
RAGAS_LLM_MODEL="ollama_chat/gemma4:26b" \
RAGAS_LLM_BASE_URL="http://host.docker.internal:11434/v1" \
RAGAS_LLM_PROVIDER="openai" \
RAGAS_LLM_API_KEY="local" \
RAGAS_LLM_ADAPTER="auto" \
RAGAS_LLM_MAX_TOKENS="8192" \
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

Fastest preset (lower quality, quicker iteration):

```bash
RAGAS_LLM_MODEL="ollama_chat/mistral:latest" \
RAGAS_LLM_BASE_URL="http://host.docker.internal:11434/v1" \
RAGAS_LLM_PROVIDER="openai" \
RAGAS_LLM_API_KEY="local" \
RAGAS_LLM_ADAPTER="auto" \
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

Max-quality preset (slower, stronger evaluator behaviour):

```bash
RAGAS_LLM_MODEL="ollama_chat/gemma4:26b" \
RAGAS_LLM_BASE_URL="http://host.docker.internal:11434/v1" \
RAGAS_LLM_PROVIDER="openai" \
RAGAS_LLM_API_KEY="local" \
RAGAS_LLM_ADAPTER="auto" \
python tests/evals/run_rag_golden_eval.py \
  --enable-ragas \
  --base-url http://host.docker.internal:18080/
```

Tip:

- Defining env vars on separate lines without `export` only sets shell variables and may not reach child processes in all shells. Use the one-shot `VAR=value command` style shown above, or run `export VAR=value` before the command.

## Qdrant Retrieval-Only Eval

Evaluates retrieval quality directly from the vector store to catch ranking/diversity regressions at the source.

Backend modes:

- `integration` (default): live Qdrant + Ollama embedding path.
- `contract`: deterministic in-memory fake SearchClient path for evaluator logic checks only (no Qdrant/Ollama dependency).

Direct runner:

```bash
OLLAMA_BASE_URL="http://host.docker.internal:11434" \
/workspaces/sc3-Azure-Foundry-RAG/.venv/bin/python tests/evals/run_qdrant_retrieval_eval.py \
  --backend-mode integration \
  --golden-set tests/evals/rag_golden_set.json \
  --index-name grounding-index \
  --qdrant-url http://host.docker.internal:6333
```

Contract-mode runner (no infra):

```bash
/workspaces/sc3-Azure-Foundry-RAG/.venv/bin/python tests/evals/run_qdrant_retrieval_eval.py \
  --backend-mode contract \
  --golden-set tests/evals/rag_golden_set.json \
  --index-name grounding-index
```

Local helper script:

```bash
bash ops/scripts/local/run-qdrant-retrieval-evals.sh
```

Default output:

- `local_state/evals/qdrant_retrieval_eval_integration_latest.json` (integration mode)
- `local_state/evals/qdrant_retrieval_eval_contract_latest.json` (contract mode)

### Qdrant Eval Environment Variables

Direct runner variables:

- `QDRANT_URL`: Default for `--qdrant-url` (default: `http://localhost:6333`).
- `AZURE_SEARCH_INDEX_NAME`: First-choice default for `--index-name`.
- `SEARCH_INDEX_NAME`: Fallback default for `--index-name`.
- `OLLAMA_BASE_URL`: Embedding endpoint used by `LocalQdrantSearchClient` (default: `http://localhost:11434`).
- `QDRANT_EVAL_BACKEND_MODE`: Default backend mode (`integration` or `contract`).

Helper script variables (`ops/scripts/local/run-qdrant-retrieval-evals.sh`):

- `QDRANT_URL`: Passed through to `--qdrant-url`.
- `AZURE_SEARCH_INDEX_NAME`: Preferred collection name.
- `SEARCH_INDEX_NAME`: Fallback collection name.

Example:

```bash
QDRANT_URL="http://localhost:6333" \
AZURE_SEARCH_INDEX_NAME="grounding-index" \
bash ops/scripts/local/run-qdrant-retrieval-evals.sh
```

Troubleshooting (`all-zero` scores on every case):

- Ensure you run with the project virtual environment interpreter.
- Ensure `OLLAMA_BASE_URL` points to a reachable Ollama service from this workspace.
- Confirm Qdrant has points: `POST /collections/<index>/points/count`.

## Notes

- Ensure the target endpoint and collection exist before running.
- Use the project virtual environment for consistent dependency resolution.
