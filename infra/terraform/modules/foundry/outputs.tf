output "foundry_account_id" {
  value = azurerm_cognitive_account.foundry.id
}

output "foundry_account_name" {
  value = azurerm_cognitive_account.foundry.name
}

output "foundry_project_id" {
  value = azapi_resource.foundry_project.id
}
