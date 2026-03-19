# Bootstrap Stack

Creates remote state storage, a bootstrap Key Vault, and minimal prerequisites for Terraform execution.

## Purpose

This stack creates:

- Terraform state resource group
- Storage account + `tfstate` container (RBAC enabled, public network access enabled)
  - Delete protection for backend storage via Terraform `prevent_destroy` and Azure management lock
- Optional bootstrap Key Vault (RBAC enabled, public network access enabled)

It then outputs values consumed by environment backend configuration.

Note: The bootstrap Key Vault pattern in this repository is a standalone deployment convenience for demonstration. In production environments, keys and secrets are expected to be managed independently (lifecycle, ownership, access model, and private networking) after jump host creation.

Note: The Terraform backend storage account is intentionally protected against accidental deletion. The bootstrap configuration applies both Terraform `prevent_destroy` protection and an Azure `CanNotDelete` management lock. Removing the backend storage account therefore requires an explicit, deliberate unlock/removal step before deletion.

## Inputs

- `location`: Azure location for bootstrap resources.
- `resource_group_name`: Resource group name for Terraform state.
- `storage_account_name_prefix`: Prefix used to generate globally unique storage account names.
- `enable_bootstrap_key_vault`: Toggle for optional bootstrap Key Vault creation and RBAC wiring.
- `key_vault_name_prefix`: Prefix used to generate globally unique Key Vault names.
- `key_vault_extra_rbac_principal_object_ids`: Optional list of extra Entra object IDs granted `Key Vault Secrets Officer` in addition to the Terraform caller identity.

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
- `TF_ENABLE_BOOTSTRAP_KEY_VAULT` (default `true`)
- `TF_KEY_VAULT_PREFIX` (default `kvtfstate`)
- `TF_KEY_VAULT_EXTRA_RBAC_OBJECT_IDS` (comma-separated Entra object IDs)

The script updates `infra/terraform/environments/<env>/backend.hcl` with the generated state storage details.

When `TF_ENABLE_BOOTSTRAP_KEY_VAULT=false`, bootstrap Key Vault creation and phase2 jumpbox key publish are both disabled.
Enterprise implementations are expected to modify or replace the publish workflow in [ops/scripts/phase2-network-dns.sh](ops/scripts/phase2-network-dns.sh) to integrate with organisation-owned key and secret lifecycle controls.

## Teardown

Destroy bootstrap only after the main environment stack has already been destroyed.

Typical order:

1. Destroy the root environment stack in `infra/terraform/`
2. Remove backend protection controls
3. Destroy the bootstrap stack in `infra/terraform/bootstrap/`

Example bootstrap destroy:

```bash
terraform -chdir=infra/terraform/bootstrap destroy \
  -input=false \
  -var-file=terraform.tfvars
```

Before bootstrap destroy can remove the Terraform state storage account, you may need to explicitly remove:

- the Terraform `prevent_destroy` protection on the backend storage resource
- the Azure `CanNotDelete` management lock applied to the backend resource group or storage account

This deliberate extra step is expected and prevents accidental removal of the Terraform backend while environments still depend on it.
