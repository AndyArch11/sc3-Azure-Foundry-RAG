# Container App Environment — VNet-integrated on the delegated agent subnet.
# Workload Profiles mode enables the Consumption profile (serverless, pay-per-use).
resource "azurerm_container_app_environment" "this" {
	name                           = "cae-${var.suffix}"
	location                       = var.location
	resource_group_name            = var.resource_group_name
	infrastructure_resource_group_name = "ME_cae-${var.suffix}_${var.resource_group_name}_${var.location}"
	log_analytics_workspace_id     = var.log_analytics_workspace_id
	infrastructure_subnet_id       = var.delegated_agent_subnet_id
	internal_load_balancer_enabled = true
	tags                           = var.tags

	workload_profile {
		name                  = "Consumption"
		workload_profile_type = "Consumption"
	}
}

# Container App Job — manually triggered ingestion runner.
# Default args run the indexer pipeline only (files must already be in blob).
# Override args at trigger time to upload-and-index in one step:
#   az containerapp job start -n <name> -g <rg> \
#     --args '--mode' 'azure' '--input-dir' '/path/to/files'
resource "azurerm_container_app_job" "ingestion" {
	count                        = var.enable_ingestion_job ? 1 : 0
	name                         = "caj-ingestion-${var.suffix}"
	location                     = var.location
	resource_group_name          = var.resource_group_name
	container_app_environment_id = azurerm_container_app_environment.this.id
	workload_profile_name        = "Consumption"
	replica_timeout_in_seconds   = 3600
	replica_retry_limit          = 1

	manual_trigger_config {
		parallelism              = 1
		replica_completion_count = 1
	}

	identity {
		type         = "UserAssigned"
		identity_ids = [var.agent_runtime_identity_id]
	}

	registry {
		server   = var.acr_login_server
		identity = var.agent_runtime_identity_id
	}

	template {
		container {
			name   = "ingestion-runner"
			image  = "${var.acr_login_server}/ingestion-runner:latest"
			cpu    = 1.0
			memory = "2Gi"
			args   = ["--mode", "azure", "--skip-upload"]

			env {
				name  = "AZURE_CLIENT_ID"
				value = var.agent_runtime_client_id
			}
			env {
				name  = "AZURE_SEARCH_ENDPOINT"
				value = var.azure_search_endpoint
			}
			env {
				name  = "AZURE_OPENAI_ENDPOINT"
				value = var.azure_openai_endpoint
			}
			env {
				name  = "AZURE_STORAGE_ACCOUNT_NAME"
				value = var.storage_account_name
			}
			env {
				name  = "AZURE_STORAGE_RESOURCE_ID"
				value = var.storage_account_id
			}
			env {
				name  = "EMBEDDING_DEPLOYMENT_NAME"
				value = var.embedding_deployment_name
			}
			env {
				name  = "EMBEDDING_DIMENSIONS"
				value = tostring(var.embedding_dimensions)
			}
		}
	}

	tags = var.tags
}

# Allow the agent runtime MI to start (and stop) the ingestion job.
# Contributor is scoped to just this job resource — not the resource group.
resource "azurerm_role_assignment" "ingestion_job_contributor" {
  count                = var.enable_ingestion_job ? 1 : 0
  scope                = azurerm_container_app_job.ingestion[0].id
  role_definition_name = "Contributor"
  principal_id         = var.agent_runtime_principal_id
}
