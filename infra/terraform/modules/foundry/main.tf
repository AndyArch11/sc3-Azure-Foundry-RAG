resource "azurerm_cognitive_account" "foundry" {
  name                          = "foundry-${var.suffix}"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "AIServices"
  sku_name                      = "S0"
  public_network_access_enabled = false
  custom_subdomain_name         = "foundry-${var.suffix}"
  project_management_enabled    = true
  tags                          = var.tags

  identity {
    type = "SystemAssigned"
  }
}

# Project creation for Foundry frequently requires preview API support.
resource "azapi_resource" "foundry_project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview"
  name                      = "project-main"
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false
  body = {
    location = var.location
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      description = "Primary project for hosted agent runtime"
    }
  }
}
