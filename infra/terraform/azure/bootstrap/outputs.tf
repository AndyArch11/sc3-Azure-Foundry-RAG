output "resource_group_name" {
  value = azurerm_resource_group.state.name
}

output "storage_account_name" {
  value = azurerm_storage_account.state.name
}

output "container_name" {
  value = azurerm_storage_container.state.name
}

output "key_vault_name" {
  value = try(azurerm_key_vault.state[0].name, null)
}

output "key_vault_id" {
  value = try(azurerm_key_vault.state[0].id, null)
}

output "key_vault_uri" {
  value = try(azurerm_key_vault.state[0].vault_uri, null)
}
