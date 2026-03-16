resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

data "azurerm_client_config" "current" {}

locals {
  key_vault_rbac_principal_object_ids = distinct(compact(concat(
    [data.azurerm_client_config.current.object_id],
    var.key_vault_extra_rbac_principal_object_ids,
  )))
}

resource "azurerm_resource_group" "state" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "state" {
  name                            = substr(lower("${var.storage_account_name_prefix}${random_string.suffix.result}"), 0, 24)
  resource_group_name             = azurerm_resource_group.state.name
  location                        = azurerm_resource_group.state.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  public_network_access_enabled   = true
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  tags                            = var.tags
}

resource "azurerm_storage_container" "state" {
  name               = "tfstate"
  storage_account_id = azurerm_storage_account.state.id
}

# Standalone demo convenience: this vault is created with public network access
# and stores non-secret bootstrap/runtime convenience values (e.g. SSH public key).
# Production environments should use independently managed key lifecycle and network controls.
resource "azurerm_key_vault" "state" {
  count                         = var.enable_bootstrap_key_vault ? 1 : 0
  name                          = substr(lower("${var.key_vault_name_prefix}${random_string.suffix.result}"), 0, 24)
  location                      = azurerm_resource_group.state.location
  resource_group_name           = azurerm_resource_group.state.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  public_network_access_enabled = true
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  tags                          = var.tags
}

resource "azurerm_role_assignment" "key_vault_secrets_officer" {
  for_each             = var.enable_bootstrap_key_vault ? { for id in local.key_vault_rbac_principal_object_ids : id => id } : {}
  scope                = azurerm_key_vault.state[0].id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = each.value
}
