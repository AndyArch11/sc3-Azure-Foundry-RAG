output "storage_account_id" { value = azurerm_storage_account.this.id }
output "storage_account_name" { value = azurerm_storage_account.this.name }
output "search_service_id" { value = azurerm_search_service.this.id }
output "search_service_name" { value = azurerm_search_service.this.name }
output "search_service_principal_id" { value = try(azurerm_search_service.this.identity[0].principal_id, null) }
output "cosmosdb_account_id" { value = azurerm_cosmosdb_account.this.id }
output "cosmosdb_account_name" { value = azurerm_cosmosdb_account.this.name }
