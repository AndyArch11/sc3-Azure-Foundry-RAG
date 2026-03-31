output "storage_account_id" { value = azurerm_storage_account.this.id }
output "storage_account_name" { value = azurerm_storage_account.this.name }
output "search_service_id" { value = azurerm_search_service.this.id }
output "search_service_name" { value = azurerm_search_service.this.name }
output "search_service_principal_id" { value = try(azurerm_search_service.this.identity[0].principal_id, null) }

output "acr_id" { value = azurerm_container_registry.this.id }
output "acr_login_server" { value = azurerm_container_registry.this.login_server }
output "acr_name" { value = azurerm_container_registry.this.name }
output "cosmosdb_account_id" { value = azurerm_cosmosdb_account.this.id }
output "cosmosdb_account_name" { value = azurerm_cosmosdb_account.this.name }
