# AGENT.md — Terraform

## Scope

Infrastructure provisioning and lifecycle management via Terraform.

## Working Directory

infra/terraform/

## Validation Before Apply

```bash
terraform fmt modules/* main.tf
terraform validate
terraform plan -var-file=environments/dev/bootstrap.generated.tfvars -var-file=environments/dev/dev.tfvars  # or prod/test
```

## Typical Apply Patterns

**Full stack (rare):**
```bash
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars -var-file=environments/dev/dev.tfvars -auto-approve
```

**Module-scoped recovery (common):**
```bash
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars -var-file=environments/dev/dev.tfvars -target=module.identity
terraform apply -var-file=environments/dev/bootstrap.generated.tfvars -var-file=environments/dev/dev.tfvars -target=module.agent_hosting
```

## Key Variable Patterns

- `query_web_image_tag`: immutable tag; use timestamp-hash format
- `cosmos_database_name`, `cosmos_container_name`: match runtime env vars
- Environment-specific values: use `environments/{dev,test,prod}/<env>.tfvars`
- Prompt injection validator controls:
	- `prompt_injection_validator_enabled`
	- `prompt_injection_validator_mode`
	- `prompt_injection_validator_threshold`
	- `prompt_injection_validator_timeout_s`
	- `prompt_injection_validator_deployment` (optional explicit deployment override)
	- `validator_model` (versioned deployment object: name/version/capacity)
- Deployment precedence:
	- If `prompt_injection_validator_deployment` is set, use it.
	- Otherwise, use `validator_model.name`.
- Rollout policy:
	- Enable validator in `shadow` mode first (especially dev/test).
	- Move to `enforce` only after observing acceptable false-positive rates.

## Cosmos and Identity Guardrails

- Data-plane role scope must target specific container: `/subscriptions/.../dbs/<db>/colls/<container>`
- Never use account-level or root scope for data contributor roles.
- Partition key on all Cosmos items must be user_id for isolation.

## Definition of Done for Terraform Changes

- Format passes.
- Validate passes.
- Plan output is reviewed and understood.
- No regressions to networking, identity, or private endpoint constraints.
