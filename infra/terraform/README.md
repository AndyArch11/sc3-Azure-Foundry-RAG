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

## Known Terraform Behaviors (Do Not Re-Debug)

### 1) Persistent drift on query web Container App workload profile

Symptom during plan:

- `module.agent_hosting.azurerm_container_app.query_web[0]` shows `workload_profile_name = "Consumption" -> null`
- Plan remains `0 to add, 1 to change, 0 to destroy` even after successful apply.

What is happening:

- Azure Container Apps keeps `workloadProfileName` as `Consumption` for apps created in a Consumption workload profile environment.
- In this configuration, the field behaves as create-time/immutable from Terraform and Azure CLI perspective.
- Repeated apply attempts can report success but the field remains `Consumption` in Azure.

How to treat it:

- Treat this as known cosmetic drift unless you intentionally plan to recreate the Container App.
- Do not spend additional cycles trying to force this field to `null` in-place.

Verification command:

- `az containerapp show -n ca-rag-query-<suffix> -g <resource-group> --query properties.workloadProfileName -o tsv`

### 2) Hardened storage account requires Azure AD auth in Terraform provider

Symptom during plan/refresh:

- `403` with message similar to `Key based authentication is not permitted on this storage account`.

Required fix:

- Ensure provider uses Azure AD auth by setting `storage_use_azuread = true` in the `azurerm` provider.

### 3) Stale remote state lock after interrupted apply

Symptom:

- `Error acquiring the state lock` with a lock ID and `OperationTypeApply`.

Recovery:

1. Confirm no active Terraform process.
2. Force unlock using the lock ID from the error:
   - `terraform -chdir=infra/terraform force-unlock -force <LOCK_ID>`

Notes:

- Prefer normal locking for day-to-day runs.
- Use `-lock=false` only as a short-lived recovery workaround.
