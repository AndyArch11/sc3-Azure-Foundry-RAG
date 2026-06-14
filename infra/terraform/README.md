# Terraform Layout

This directory is now a multi-cloud container.

## Canonical stacks

- `azure/`: canonical Azure Terraform stack
- `aws/`: AWS Terraform stack

New documentation and new scripts should prefer the canonical Azure path:

- `terraform -chdir=infra/terraform/azure init ...`
- `terraform -chdir=infra/terraform/azure plan ...`
- `terraform -chdir=infra/terraform/azure apply ...`

## Azure Layout

- `azure/bootstrap/`: Azure backend bootstrap and prerequisite shared resources
- `azure/modules/`: reusable Azure modules
- `azure/environments/`: Azure environment overlays (`dev`, `test`, `prod`)

Environment-specific tfvars under `azure/environments/<env>/` are the authoritative Azure inputs for this repository.
