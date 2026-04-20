# Module: app_secrets

Creates a private Key Vault for application secrets and connects it through a
private endpoint inside the workload VNet.

## Resources

- `azurerm_key_vault` with `public_network_access_enabled = false`
- `azurerm_private_endpoint` targeting subresource `vault`
- `azurerm_role_assignment` granting `Key Vault Secrets Officer` to the
  shared runtime/jumpbox managed identity

## Notes

- DNS uses `privatelink.vaultcore.azure.net` via the platform DNS module.
- This Key Vault is intended for runtime app secrets such as query web
  EasyAuth client credentials.
