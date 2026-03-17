output "agent_environment_id" {
  value = azurerm_container_app_environment.this.id
}

output "container_app_job_name" {
  value = try(azurerm_container_app_job.ingestion[0].name, null)
}
