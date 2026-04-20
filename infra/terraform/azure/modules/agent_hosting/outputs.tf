output "agent_environment_id" {
  value = azurerm_container_app_environment.this.id
}

output "container_app_job_name" {
  value = try(azurerm_container_app_job.ingestion[0].name, null)
}

output "query_web_app_name" {
  value = try(azurerm_container_app.query_web[0].name, null)
}

output "query_web_fqdn" {
  value = try(azurerm_container_app.query_web[0].ingress[0].fqdn, null)
}

output "confluence_poller_app_name" {
  value = try(azurerm_container_app.confluence_poller[0].name, null)
}
