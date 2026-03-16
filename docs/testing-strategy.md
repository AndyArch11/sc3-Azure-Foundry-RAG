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

## Test Data

- Small sanitised PDF fixtures.
- Small Excel fixtures representing multi-sheet controls.
- Deterministic expected outputs where practical.

## Exit Criteria per Phase

- All unit tests pass for pull requests.
- Private integration smoke tests pass before promote-to-prod.
