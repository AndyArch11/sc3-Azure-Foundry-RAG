output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.this.id
}

output "azure_monitor_workspace_id" {
  value = azurerm_monitor_workspace.prometheus.id
}
