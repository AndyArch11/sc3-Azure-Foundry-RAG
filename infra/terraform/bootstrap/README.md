# Bootstrap Stack

Creates remote state storage and minimal prerequisites for Terraform execution.

## Purpose

This stack creates the Terraform state resource group and storage container, then outputs values consumed by environment backend configuration.

## Inputs

- `location`: Azure location for bootstrap resources.
- `resource_group_name`: Resource group name for Terraform state.
- `storage_account_name_prefix`: Prefix used to generate globally unique storage account names.

## Execute Phase 1

Run from repository root:

```bash
./ops/scripts/phase1-bootstrap.sh dev
```

Optional environment variables:

- `TF_LOCATION` (default `australiaeast`)
- `TF_STATE_RESOURCE_GROUP` (default `rg-tfstate-<env>`)
- `TF_STATE_STORAGE_PREFIX` (default `sttfstate<env>`)
- `TF_BACKEND_KEY` (default `platform/<env>.tfstate`)

The script updates `infra/terraform/environments/<env>/backend.hcl` with the generated state storage details.
