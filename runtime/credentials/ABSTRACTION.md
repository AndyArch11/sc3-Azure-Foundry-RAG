# Credentials Abstraction Contract

## Purpose
Provide a cloud-agnostic credential surface used by runtime and query services.

## Proposed Interface
- Resolve cloud provider from configuration.
- Return provider-specific credential/session object for downstream clients.
- Avoid leaking provider SDK details into business logic modules.

## Design Constraints
- Azure path must continue to use existing managed identity/default credential behavior.
- AWS path should support IAM role/session usage without changing business logic.
- Local path should support no-credential or mock credentials for development.

## Factory Pattern
- Module exposes a single factory entrypoint (for example get_credential_provider).
- Provider selected by environment setting (for example CLOUD_PROVIDER).

## Test Requirements
- Factory dispatch test coverage.
- Adapter contract tests for Azure, AWS, and Local providers.
- Failure-path tests for invalid provider configuration.
