resource "azurerm_user_assigned_identity" "agent_runtime" {
  name                = "id-agent-runtime-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = var.scope_ids.storage
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

resource "azurerm_role_assignment" "search_index_contributor" {
  scope                = var.scope_ids.search
  role_definition_name = "Search Index Data Contributor"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

resource "azurerm_role_assignment" "cognitive_services_user" {
  scope                = var.scope_ids.foundry
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

resource "azurerm_role_assignment" "foundry_project_manager" {
  scope                = var.scope_ids.foundry
  role_definition_name = "Azure AI Project Manager"
  principal_id         = var.deployment_principal_object_id
}
