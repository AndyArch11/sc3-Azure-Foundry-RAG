output "resource_group_name" {
  value = module.foundation.resource_group_name
}

output "vnet_id" {
  value = local.use_byol_network ? var.byol_vnet_id : try(module.network[0].vnet_id, null)
}

output "search_endpoint" {
  value = "https://${module.data_services.search_service_name}.search.windows.net"
}

output "openai_endpoint" {
  value = "https://${module.foundry.foundry_account_name}.openai.azure.com"
}

output "ai_services_endpoint" {
  value = "https://${module.foundry.foundry_account_name}.cognitiveservices.azure.com"
}

output "storage_account_name" {
  value = module.data_services.storage_account_name
}

output "foundry_project_id" {
  value = module.foundry.foundry_project_id
}

output "log_analytics_workspace_id" {
  value = module.observability.log_analytics_workspace_id
}

output "azure_monitor_workspace_id" {
  value = module.observability.azure_monitor_workspace_id
}

output "azure_monitor_data_collection_endpoint_id" {
  value = module.observability.monitor_data_collection_endpoint_id
}

output "azure_monitor_data_collection_rule_id" {
  value = module.observability.monitor_data_collection_rule_id
}

output "acr_login_server" {
  value = module.data_services.acr_login_server
}

output "container_app_job_name" {
  value = module.agent_hosting.container_app_job_name
}

output "query_web_app_name" {
  value = module.agent_hosting.query_web_app_name
}

output "query_web_fqdn" {
  value = module.agent_hosting.query_web_fqdn
}

output "confluence_poller_app_name" {
  value = module.agent_hosting.confluence_poller_app_name
}

output "query_web_entra_client_id" {
  value = try(azuread_application.query_web[0].client_id, null)
}

output "app_secrets_key_vault_name" {
  value = try(module.app_secrets[0].key_vault_name, null)
}

output "app_secrets_key_vault_id" {
  value = try(module.app_secrets[0].key_vault_id, null)
}

output "agent_runtime_principal_id" {
  value = module.identity.agent_runtime_principal_id
}
