output "resource_group_name" {
  value = module.foundation.resource_group_name
}

output "vnet_id" {
  value = module.network.vnet_id
}

output "search_endpoint" {
  value = "https://${module.data_services.search_service_name}.search.windows.net"
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

output "acr_login_server" {
  value = module.data_services.acr_login_server
}

output "container_app_job_name" {
  value = module.agent_hosting.container_app_job_name
}
