# Terraform Layout

- `bootstrap/`: state backend and prerequisite shared resources.
- `modules/`: reusable modules for platform capabilities.
- `environments/`: environment overlays (`dev`, `test`, `prod`).

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
