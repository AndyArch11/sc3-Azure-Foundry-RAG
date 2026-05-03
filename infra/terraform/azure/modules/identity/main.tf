data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "agent_runtime" {
  name                = trimspace(var.agent_runtime_identity_name_override) != "" ? var.agent_runtime_identity_name_override : "id-agent-runtime-${var.suffix}"
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

resource "azurerm_cosmosdb_sql_role_assignment" "cosmos_data_contributor" {
  resource_group_name = var.resource_group_name
  account_name        = split("/", var.scope_ids.cosmos)[8]
  role_definition_id  = "${var.scope_ids.cosmos}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.agent_runtime.principal_id
  scope               = "${var.scope_ids.cosmos}/dbs/${var.cosmos_database_name}/colls/${var.cosmos_container_name}"
}

resource "azurerm_cosmosdb_sql_role_assignment" "cosmos_orchestration_data_contributor" {
  resource_group_name = var.resource_group_name
  account_name        = split("/", var.scope_ids.cosmos)[8]
  role_definition_id  = "${var.scope_ids.cosmos}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.agent_runtime.principal_id
  scope               = "${var.scope_ids.cosmos}/dbs/${var.cosmos_database_name}/colls/${var.cosmos_orchestration_container_name}"
}

resource "azurerm_role_assignment" "foundry_project_manager" {
  scope                = var.scope_ids.foundry
  role_definition_name = "Azure AI Project Manager"
  principal_id         = var.deployment_principal_object_id
}

# Search service system-assigned MI — required for blob indexer and embedding skill.
# principal_id may be unknown during plan, but Terraform can still plan these
# resources and resolve the value during apply.
resource "azurerm_role_assignment" "search_mi_storage_blob_reader" {
  scope                = var.scope_ids.storage
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.search_service_principal_id
}

resource "azurerm_role_assignment" "search_mi_openai_user" {
  scope                = var.scope_ids.foundry
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.search_service_principal_id
}

resource "azurerm_role_assignment" "search_mi_cognitive_services_user" {
  scope                = var.scope_ids.foundry
  role_definition_name = "Cognitive Services User"
  principal_id         = var.search_service_principal_id
}

# Allow jumpbox diagnostics queries (private endpoints, DNS zones, etc.)
resource "azurerm_role_assignment" "agent_runtime_network_reader" {
  scope                = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${var.resource_group_name}"
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

# Jumpbox terraform execution needs write permissions for agent hosting resources
# (for example Microsoft.App/managedEnvironments/write).
resource "azurerm_role_assignment" "agent_runtime_rg_contributor" {
  scope                = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${var.resource_group_name}"
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
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

# Required by azurerm when managing Container App Environment wired to Log Analytics;
# provider reads workspace shared keys via Microsoft.OperationalInsights/workspaces/sharedKeys/action.
resource "azurerm_role_assignment" "log_analytics_contributor" {
  scope                = var.scope_ids.log_analytics
  role_definition_name = "Log Analytics Contributor"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}

# CosmosDB control-plane RBAC: allows jumpbox/terraform to manage cosmos account.
# Includes permission to list keys (needed by azurerm provider for plan/apply).
# Backend-state RBAC (Storage Blob Data Contributor and Reader on the tfstate
# storage account) is managed in the bootstrap stack, not here, so that the
# RBAC assignments survive a full destroy of this stack without revoking the
# deploying identity's write access to remote state mid-run.
resource "azurerm_role_assignment" "cosmosdb_account_contributor" {
  scope                = var.scope_ids["cosmos"]
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.agent_runtime.principal_id
}


