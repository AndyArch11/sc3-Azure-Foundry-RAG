locals {
  # Key Vault names must be globally unique and <= 24 chars.
  key_vault_name = substr("kvapp${replace(var.suffix, "-", "")}", 0, 24)
}

resource "azurerm_key_vault" "this" {
  name                          = local.key_vault_name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  public_network_access_enabled = false
  soft_delete_retention_days    = 7
  purge_protection_enabled      = false
  tags                          = var.tags
}

data "azurerm_client_config" "current" {}

resource "azurerm_private_endpoint" "this" {
  name                = "pe-kv-app-secrets"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-kv-app-secrets"
    private_connection_resource_id = azurerm_key_vault.this.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "zg-kv-app-secrets"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}

# Shared runtime/jumpbox managed identity can write and read secrets.
resource "azurerm_role_assignment" "agent_runtime_kv_secrets_officer" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.agent_runtime_principal_id
}
