# Testing Strategy

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
./ops/scripts/run-query-web-integration-tests.sh \
  "https://<query-web-fqdn>" \
  "<optional-auth-token>"
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

Runner preflight flags:

- `QUERY_WEB_PREFLIGHT` (default: `true`) performs DNS + `/health` checks before pytest
- `QUERY_WEB_PREFLIGHT_ONLY` (default: `false`) runs only preflight checks and exits

Example preflight-only check:

```bash
QUERY_WEB_PREFLIGHT_ONLY=true \
./ops/scripts/run-query-web-integration-tests.sh "https://<query-web-fqdn>"
```

The suite validates:

- `/health` and `/api/config`
- conversation create/add/list/history endpoints
- optional `/api/ask` request when enabled

## Test Data

- Small sanitised PDF fixtures.
- Small Excel fixtures representing multi-sheet controls.
- Deterministic expected outputs where practical.

## Exit Criteria per Phase

- All unit tests pass for pull requests.
- Private integration smoke tests pass before promote-to-prod.
