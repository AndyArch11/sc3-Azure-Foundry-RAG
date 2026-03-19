# AGENT.md — Testing

## Scope

Unit and integration test workflows.

## Unit Tests (local, fast)

```bash
python3 -m pytest tests/unit/ -q
```

Run after app logic, conversation, or extractor changes.

## Integration Tests (private network, slow)

Tests designed to run from jump host or private network context only.

**From jump host:**
```bash
./ops/scripts/run-query-web-integration-tests.sh "https://<query_web_fqdn>"
```

Optional flags:
```bash
QUERY_WEB_PREFLIGHT=true                # DNS + /health pre-check
QUERY_WEB_REQUIRE_CONVERSATIONS=true    # Strict conversation API checks
QUERY_WEB_RUN_API_ASK=true              # Include /api/ask test
```

## Markers

- `@pytest.mark.unit`: fast, no network
- `@pytest.mark.integration`: requires Container App access
- `@pytest.mark.private_network`: requires DNS resolution from private network

## Definition of Done for Test Changes

- New tests are exercisable.
- Existing tests still pass.
- Integration tests can be run from jump host without modification.
