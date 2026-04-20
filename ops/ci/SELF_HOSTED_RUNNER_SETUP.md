# Self-Hosted CI Runner Setup (Private Network)

## Objective
Run CI workflows that can access private resources (for example private endpoints, internal DNS, and non-public test infrastructure).

## Recommended Baseline
- Platform: GitHub Actions self-hosted runner.
- Placement: private subnet/jumpbox network segment with required outbound egress.
- Access model: least-privilege identity and scoped secrets.

## Runner Requirements
- Linux host with Docker and Git.
- Stable outbound access to GitHub control plane.
- Access to private dependencies needed by integration tests.
- Access to container registries used by this repo.

## Security Controls
- Use ephemeral or autoscaled runners where possible.
- Rotate runner registration tokens regularly.
- Use short-lived cloud credentials where possible.
- Restrict repository and workflow permissions.
- Centralise audit logging for runner activity.

## Pipeline Stages (Target)
- Lint and format checks.
- Type checks.
- Unit tests.
- Integration tests (private-network capable).
- Security/static analysis.
- Terraform fmt/validate.

## Open Decisions
- Final runner host location (jumpbox vs dedicated CI subnet).
- Secret source (GitHub encrypted secrets vs cloud secret manager pull-at-runtime).
- Whether integration tests run on every PR or only gated branches.

## Next Actions
1. Confirm runner placement architecture.
2. Add initial workflow skeleton in .github/workflows.
3. Validate network access to private dependencies from runner host.
4. Enable required branch protection checks.
