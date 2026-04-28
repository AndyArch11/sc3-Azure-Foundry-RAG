# Terraform Runner Container

Container image used to run Terraform and Azure authentication workflows in a deterministic environment.

## Included Tooling

- Terraform
- Azure CLI (`az`)
- `jq`, `git`, `curl`, `bash`

## Build

```bash
docker build -t tf-runner:local ops/containers/terraform-runner
```

## If Docker Is Not Available

Install Terraform directly in the current Linux environment:

```bash
./ops/scripts/local/install-terraform-local.sh
```

This allows all Terraform scripts in this repository to run without Docker.

## Quick Verification

```bash
docker run --rm tf-runner:local terraform version
```

## Run Against Repository

```bash
docker run --rm -it \
  -v "$PWD":/workspace \
  -w /workspace \
  tf-runner:local bash
```
