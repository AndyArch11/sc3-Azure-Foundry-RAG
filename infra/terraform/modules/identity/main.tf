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

resource "azurerm_role_assignment" "search_service_contributor" {
  scope                = var.scope_ids.search
  role_definition_name = "Search Service Contributor"
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

# Search service system-assigned MI — required for blob indexer and embedding skill.
# count guard: principal_id is null until the first apply enables the identity;
# a second apply creates the role assignments once the value is known.
resource "azurerm_role_assignment" "search_mi_storage_blob_reader" {
  count                = var.search_service_principal_id != null ? 1 : 0
  scope                = var.scope_ids.storage
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.search_service_principal_id
}

resource "azurerm_role_assignment" "search_mi_openai_user" {
  count                = var.search_service_principal_id != null ? 1 : 0
  scope                = var.scope_ids.foundry
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.search_service_principal_id
}

# Agent runtime MI — AcrPull for Container App Job image pull;
# AcrPush for jumpbox / CI build-and-push workflow.
resource "azurerm_role_assignment" "acr_pull" {
  scope                = var.scope_ids.acr
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

resource "azurerm_role_assignment" "acr_push" {
  scope                = var.scope_ids.acr
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

resource "azurerm_role_assignment" "log_analytics_reader" {
  scope                = var.scope_ids.log_analytics
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

resource "azurerm_role_assignment" "log_analytics_workspace_reader" {
  scope                = var.scope_ids.log_analytics
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}
