# Hosted agent runtime resources are implemented via preview APIs.
# Placeholder resources are represented with azapi for subsequent phases.
resource "azapi_resource" "agent_environment" {
  type                      = "Microsoft.App/managedEnvironments@2024-10-02-preview"
  name                      = "agent-env-${var.suffix}"
  parent_id                 = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${var.resource_group_name}"
  schema_validation_enabled = false

  body = {
    location = var.location
    properties = {
      vnetConfiguration = {
        infrastructureSubnetId = var.delegated_agent_subnet_id
      }
    }
    tags = var.tags
  }
}

resource "azapi_resource" "hosted_query_agent" {
  type      = "Microsoft.CognitiveServices/accounts/projects/agents@2025-04-01-preview"
  name      = "query-agent"
  parent_id = var.foundry_project_id
  schema_validation_enabled = false

  timeouts {
    create = "30m"
    read   = "30m"
    update = "30m"
    delete = "30m"
  }

  body = {
    properties = {
      modelInstructions = {
        queryModel      = var.query_model
        embeddingModel  = var.embedding_model
        evaluationModel = var.evaluation_model
      }
      description = "Query agent scaffold"
    }
  }
}

data "azurerm_client_config" "current" {}
