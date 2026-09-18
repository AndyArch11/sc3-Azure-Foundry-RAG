# Testing Strategy

Operational convention:

- Use `TARGET_ENV` when selecting environment-specific Terraform state and tfvars.
- Run private-network tests only from a host with line of sight into the VNet.
- Prefer immutable image tags and Terraform rollouts before executing smoke tests against a new revision.

## Unit Tests (Primary, Automated)

Unit tests are designed to run in any CI environment because they do not require access to private Azure endpoints.

### Coverage Areas

- Terraform static checks:
  - formatting
  - validate
  - module contract checks
- Python runtime units:
  - configuration parsing
  - request shaping for agent calls
  - ingestion chunking decisions
  - telemetry enrichment logic

## Integration Tests (Private Network Aware)

Integration tests that call private endpoints must run from a trusted network location.

### Supported Execution Locations

- Jumpbox VM reached through Bastion (standalone/sandbox convenience, or SMB deployments without mature network controls).
- Self-hosted CI runner deployed inside the VNet (typical enterprise pattern).

In larger enterprise environments with mature private networking, jumpbox access is usually unnecessary and tests should run from enterprise-controlled private runners.

### Suggested Split

- `tests/unit/`: default pipeline, always on pull requests.
- `tests/integration/`: scheduled and release-gated, executed in private network only.

### Jump Host Execution

Run integration smoke tests from a private-network context (for example, jumpbox VM):

```bash
TARGET_ENV="<env>"
QUERY_FQDN=$(terraform -chdir=infra/terraform/azure output -raw query_web_fqdn)

./ops/scripts/azure/run-query-web-integration-tests.sh \
  "https://${QUERY_FQDN}" \
  "<optional-auth-token>"
```

Recommended pre-test rollout flow:

```bash
TARGET_ENV="<env>"
QUERY_TAG="$(date +%Y%m%d%H%M)-<gitsha>"

ENV="${TARGET_ENV}" IMAGE_TAG="${QUERY_TAG}" ./ops/scripts/azure/build-push-query-web.sh

terraform -chdir=infra/terraform/azure apply \
  -input=false \
  -var-file="environments/${TARGET_ENV}/bootstrap.generated.tfvars" \
  -var-file="environments/${TARGET_ENV}/${TARGET_ENV}.tfvars" \
  -var "query_web_image_tag=${QUERY_TAG}" \
  -target=module.agent_hosting
```

Environment variables supported by the test suite:

- `QUERY_WEB_BASE_URL` (required if not passed as arg)
- `QUERY_WEB_AUTH_TOKEN` (required only when app auth is enabled)
- `QUERY_WEB_TIMEOUT_S` (default: `30`)
- `QUERY_WEB_INSECURE_TLS` (`true` to disable TLS verification)
- `QUERY_WEB_RUN_API_ASK` (`true` to include `/api/ask` live call)
- `QUERY_WEB_TEST_QUESTION` (default: `What is secure-by-design?`)
- `QUERY_WEB_TEST_RETRIEVE_K` (default: `3`)
- `QUERY_WEB_TEST_TEMPERATURE` (default: `1.0`)
- `QUERY_WEB_REQUIRE_CONVERSATIONS` (default: `false`; when `true`, missing conversation routes fail instead of skip)

Runner preflight flags:

- `QUERY_WEB_PREFLIGHT` (default: `true`) performs DNS + `/health` checks before pytest
- `QUERY_WEB_PREFLIGHT_ONLY` (default: `false`) runs only preflight checks and exits

Example preflight-only check:

```bash
QUERY_WEB_PREFLIGHT_ONLY=true \
./ops/scripts/azure/run-query-web-integration-tests.sh "https://<query-web-fqdn>"
```

The suite validates:

- `/health` and `/api/config`
- conversation create/add/list/history endpoints
- conversation rating and feedback persistence when enabled
- optional `/api/ask` request when enabled

Future task:

- Add non-interactive Entra bearer-token integration tests for group-gated query web access.
- Current group gating relies on platform principal headers and is not directly testable with managed identity-only jumpbox execution.
- Extend query web auth/test harness to support JWT claim validation (groups claim) so CI/private-runner tests can verify Entra security group enforcement without manual user session context.

## Test Data

- Small sanitised PDF fixtures.
- Small Excel fixtures representing multi-sheet controls.
- Deterministic expected outputs where practical.

## Golden-Set RAG Evals

To prevent ranking regressions (for example low-k framework crowding), maintain a golden set and evaluate it on every meaningful retrieval/ranking change.

Golden-set assets:

- `tests/evals/rag_golden_set.json`: curated cases and expected framework relevance.
- `tests/evals/schemas/rag_eval_schemas.json`: suite schema for governance and review consistency.
- `tests/evals/run_rag_golden_eval.py`: live eval runner against `/api/ask`.
- `ops/scripts/local/run-rag-golden-evals.sh`: convenience wrapper.

Deterministic retrieval metrics produced per case:

- `precision_at_k`
- `framework_diversity_at_k`
- `dominant_framework_share`
- `expected_framework_coverage`

Run locally:

```bash
QUERY_WEB_BASE_URL="http://127.0.0.1:8080" \
QUERY_WEB_AUTH_TOKEN="<optional-token>" \
bash ./ops/scripts/local/run-rag-golden-evals.sh
```

Output report:

- `local_state/evals/rag_eval_latest.json`

Optional answer-quality layer (RAGAS):

```bash
ENABLE_RAGAS=true \
QUERY_WEB_BASE_URL="http://127.0.0.1:8080" \
bash ./ops/scripts/local/run-rag-golden-evals.sh
```

Notes:

- Deterministic retrieval checks should be the primary regression gate for pull requests.
- RAGAS should be treated as a secondary signal (nightly or release-candidate), because evaluator-model drift can move scores over time.
- Use temperature-0 style evaluator settings and fixed evaluator model versions where possible for reproducibility.

### Qdrant Retrieval-Only Golden Evals

When retrieval bias appears to originate in vector ranking (for example framework crowding at low-k), run retrieval-only evals directly against the Qdrant collection.

Artifacts:

- `tests/evals/run_qdrant_retrieval_eval.py`
- `ops/scripts/local/run-qdrant-retrieval-evals.sh`

Run locally:

```bash
QDRANT_URL="http://localhost:6333" \
AZURE_SEARCH_INDEX_NAME="grounding-index" \
bash ./ops/scripts/local/run-qdrant-retrieval-evals.sh
```

Output report:

- `local_state/evals/qdrant_retrieval_eval_latest.json`

This retrieval-only track should be treated as the primary gate for ranking and diversity regressions, with answer-quality RAGAS checks as a secondary signal.

## Exit Criteria per Phase

- All unit tests pass for pull requests.
- Private integration smoke tests pass before promote-to-prod.
