# Terraform Layout

- `bootstrap/`: state backend and prerequisite shared resources.
- `modules/`: reusable modules for platform capabilities.
- `environments/`: environment overlays (`dev`, `test`, `prod`).

Environment-specific tfvars under `environments/<env>/` are the authoritative operational inputs for this repository. `terraform.tfvars.example` is a minimal seed template only and should not be treated as a full mirror of any active environment file.

Apply order:

1. `bootstrap`
2. platform stack using root module in this directory

## Phase 1 Execution

1. Build Terraform runner container:
   - `docker build -t tf-runner:local ops/containers/terraform-runner`
   - If Docker is unavailable in your current environment, install Terraform locally instead:
   - `./ops/scripts/install-terraform-local.sh`
2. Authenticate to Azure (host shell or inside runner container):
   - `az login`
   - `az account set --subscription <subscription-id>`
3. Run bootstrap and write backend config:
   - `./ops/scripts/phase1-bootstrap.sh dev`

After bootstrap, initialise the environment stack:

- `terraform -chdir=infra/terraform init -backend-config=infra/terraform/environments/dev/backend.hcl`

## Environment Teardown

Destroy in this order:

1. Platform stack
2. Bootstrap stack

Destroy the platform stack first so dependent resources are removed before the remote-state backend.

Example for `dev`:

```bash
terraform -chdir=infra/terraform destroy \
   -input=false \
   -var-file=environments/dev/bootstrap.generated.tfvars \
   -var-file=environments/dev/dev.tfvars
```

After the platform stack is destroyed, remove bootstrap protections and then destroy bootstrap resources:

```bash
terraform -chdir=infra/terraform/bootstrap destroy \
   -input=false \
   -var-file=terraform.tfvars
```

If bootstrap destroy is blocked, first remove any Terraform `prevent_destroy` guardrails or Azure management locks protecting the state storage account, then rerun destroy.

## Future Considerations

- If Foundry project capability host creation intermittently fails after role assignment changes, consider adding an explicit short `time_sleep` dependency between role assignments and capability host resources to absorb AAD/RBAC propagation delay.
