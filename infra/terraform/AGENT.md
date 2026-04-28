# AGENT.md — Terraform (Multi-Cloud Root)

## Scope

Infrastructure provisioning and lifecycle management via Terraform from the shared multi-cloud root.

## Working Directories

- `infra/terraform/azure/`: canonical Azure stack
- `infra/terraform/aws/`: AWS stack

## Guidance

- Prefer cloud-specific stack directories for new work.
- Keep cloud-specific details in stack-local agent files:
  - `infra/terraform/azure/AGENT.md`
  - `infra/terraform/aws/AGENT.md`

## Validation Before Apply

Azure:

```bash
terraform -chdir=infra/terraform/azure fmt -recursive modules main.tf
terraform -chdir=infra/terraform/azure validate
terraform -chdir=infra/terraform/azure plan -var-file=environments/dev/bootstrap.generated.tfvars -var-file=environments/dev/dev.tfvars
```

AWS:

```bash
terraform -chdir=infra/terraform/aws fmt -recursive modules main.tf
terraform -chdir=infra/terraform/aws validate
terraform -chdir=infra/terraform/aws plan -var-file=environments/dev/dev.tfvars
```
