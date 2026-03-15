output "resource_group_name" {
  value = module.foundation.resource_group_name
}

output "vnet_id" {
  value = module.network.vnet_id
}

output "foundry_project_id" {
  value = module.foundry.foundry_project_id
}

output "log_analytics_workspace_id" {
  value = module.observability.log_analytics_workspace_id
}
