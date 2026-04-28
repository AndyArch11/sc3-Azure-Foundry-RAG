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
./ops/scripts/azure/run-query-web-integration-tests.sh "https://<query_web_fqdn>"
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

## Parser and Guardrail Test Expectations

**Structured output parsers** (evaluator, validator): any change to response parsing must include tests for all three response shapes:
- Raw JSON (`{"malicious": false, "confidence": 0.1}`)
- Fenced JSON (` ```json\n{...}\n``` `)
- Prose-wrapped JSON (narrative text with embedded JSON object)

**Guardrail tests** must cover all four decision paths:
- Deterministic block (regex pattern match)
- Validator shadow-mode: query proceeds regardless of validator verdict
- Validator enforce-mode block: validator returns `malicious=true` above threshold
- Validator enforce-mode allow: validator returns `malicious=false` or confidence below threshold

**Frozen dataclass config patching:** `QueryConfig` is a frozen dataclass. Do not use `patch.object(config_instance, "field", value)` — this raises `FrozenInstanceError`. Instead:
```python
from dataclasses import replace
test_config = replace(app_module.config, field=value)
with patch.object(app_module, "config", test_config):
    ...
```
