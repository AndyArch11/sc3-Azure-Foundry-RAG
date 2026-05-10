# Provider Onboarding Guide

This guide describes how to add a new cloud provider to the runtime and query-web stack without reintroducing provider-specific branching into core orchestration paths.

## Scope

- Add a provider adapter and capability mapping.
- Wire config resolution for runtime and query-web.
- Add search, storage, credential, and optional LLM integrations.
- Add dependency profiles and Docker build validation.
- Add provider contract and parity tests.

## Existing Provider Architecture

Core provider abstractions are implemented in `runtime/provider_core/`:

- `types.py`: canonical provider identity and alias normalisation.
- `protocols.py`: adapter and capability contracts.
- `registry.py`: provider adapter registry and default adapters.
- `config_resolution.py`: shared environment-to-config resolution helpers.

Current built-in providers:

- `azure`
- `aws`
- `local` (with `dev` normalised to `local`)

## Onboarding Checklist

### 1. Add Provider Identity and Registry Adapter

1. Extend `CloudProvider` in `runtime/provider_core/types.py`.
2. Implement a provider adapter in `runtime/provider_core/registry.py`:
   - capability flags
   - request mapping for search calls (`map_search_request`)
3. Register the adapter in `build_default_registry()`.

### 2. Add Config Resolution Rules

1. Extend shared provider mapping in `runtime/provider_core/config_resolution.py`.
2. Ensure both surfaces are covered:
   - runtime config load paths
   - query-web config load paths
3. Keep required-variable behaviour explicit for missing settings.

### 3. Add Runtime Service Adapters

Add or extend provider implementations in these runtime modules:

- `runtime/credentials/`
- `runtime/search/`
- `runtime/storage/`
- `runtime/llm/` (if provider requires chat/completion support)
- `runtime/state_store/` (if provider-specific state persistence is required)

Avoid direct provider string checks in orchestration code where strategy/registry dispatch already exists.

### 4. Extend Ingestion Orchestrators

If provider-specific ingestion flow is needed, add orchestrator support under:

- `runtime/ingestion/orchestrators/`

Keep provider-specific behaviour in orchestrators and helper modules, not in the top-level runner dispatch.

### 5. Add Dependency Profiles

Runtime dependency profiles live under `runtime/requirements/`:

- `base.txt` shared deps
- provider profiles (for example `azure.txt`, `aws.txt`, `local.txt`)
- service profiles (for example `ingestion.txt`, `poller.txt`)

Query-web profiles live under `query_web/requirements/`:

- `base.txt` shared query-web deps
- provider profiles (for example `azure.txt`, `aws.txt`)
- aggregate profiles (for example `cloud.txt`, `full.txt`)
- compatibility wrappers (`service-cloud.txt`, `service-full.txt`)

Add any new provider dependencies to the smallest required profile.

### 6. Validate Docker Build Paths

Verify profile-aware builds still work:

```bash
bash ./ops/scripts/local/smoke-docker-profiles.sh
```

This script validates:

- runtime image build and CLI startup
- poller image build and CLI startup
- query-web local profile image build
- query-web azure profile image build
- query-web health endpoint smoke check

### 7. Add and Run Tests

At minimum, add/extend tests for:

- provider registry dispatch and alias handling
- provider config resolution
- provider capability behaviour in query and assessment flows
- ingestion orchestrator provider dispatch

Recommended unit test modules:

- `tests/unit/test_provider_core_registry.py`
- `tests/unit/test_provider_core_config_resolution.py`
- `tests/unit/test_query_web_search_pipeline_module.py`
- `tests/unit/test_assessment_provider_strategies.py`

Run:

```bash
source runtime/.venv/bin/activate
python -m pytest tests/unit -q
```

## Definition of Done for New Provider Onboarding

1. Provider is registered in `provider_core` and resolves from env aliases.
2. Query-web and runtime config loaders resolve provider settings without duplicate ad-hoc branching.
3. Search/storage/credential integrations function via adapter dispatch.
4. Dependency profiles and Docker smoke checks pass.
5. Unit tests and provider parity/contract tests pass.
6. Documentation is updated in:
   - this onboarding guide
   - top-level `README.md`
   - any provider-specific deployment/runbook docs if needed.
