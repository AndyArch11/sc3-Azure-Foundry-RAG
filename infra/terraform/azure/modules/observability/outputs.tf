output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.this.id
}

output "azure_monitor_workspace_id" {
  value = azurerm_monitor_workspace.prometheus.id
}

output "monitor_data_collection_endpoint_id" {
  value = azurerm_monitor_data_collection_endpoint.this.id
}

output "monitor_data_collection_rule_id" {
  value = azurerm_monitor_data_collection_rule.prometheus.id
}
