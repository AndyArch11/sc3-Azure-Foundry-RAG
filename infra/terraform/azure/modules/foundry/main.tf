resource "azurerm_cognitive_account" "foundry" {
  name                          = trimspace(var.foundry_account_name_override) != "" ? var.foundry_account_name_override : "foundry-${var.suffix}"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "AIServices"
  sku_name                      = "S0"
  public_network_access_enabled = false
  custom_subdomain_name         = trimspace(var.foundry_account_name_override) != "" ? var.foundry_account_name_override : "foundry-${var.suffix}"
  project_management_enabled    = true
  tags                          = var.tags

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }

  identity {
    type = "SystemAssigned"
  }
}

locals {
  # Skip duplicate deployments when multiple roles share the same model/version.
  query_model_is_distinct = var.query_model.name != var.embedding_model.name || var.query_model.version != var.embedding_model.version
  eval_model_is_distinct  = (var.evaluation_model.name != var.embedding_model.name || var.evaluation_model.version != var.embedding_model.version) && (var.evaluation_model.name != var.query_model.name || var.evaluation_model.version != var.query_model.version)
  validator_model_is_distinct = (
    (var.validator_model.name != var.embedding_model.name || var.validator_model.version != var.embedding_model.version) &&
    (var.validator_model.name != var.query_model.name || var.validator_model.version != var.query_model.version) &&
    (var.validator_model.name != var.evaluation_model.name || var.validator_model.version != var.evaluation_model.version)
  )
}

moved {
  from = azapi_resource.model_deployment["text-embedding-ada-002"]
  to   = azapi_resource.model_deployment_embedding[0]
}

moved {
  from = azapi_resource.model_deployment["gpt-5.1-chat"]
  to   = azapi_resource.model_deployment_query[0]
}

moved {
  from = azapi_resource.model_deployment["gpt-4.1-mini"]
  to   = azapi_resource.model_deployment_evaluation[0]
}

resource "azapi_resource" "model_deployment_validator" {
  count                     = var.enable_model_deployments && local.validator_model_is_distinct ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name                      = var.validator_model.name
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.model_deployment_embedding,
    azapi_resource.model_deployment_query,
    azapi_resource.model_deployment_evaluation,
  ]

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.validator_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.validator_model.name
        version = var.validator_model.version
      }
    }
  }
}

# Deploy model endpoints in sequence to avoid concurrent account deployment operations.
resource "azapi_resource" "model_deployment_embedding" {
  count                     = var.enable_model_deployments ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name                      = var.embedding_model.name
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.embedding_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.embedding_model.name
        version = var.embedding_model.version
      }
    }
  }
}

resource "azapi_resource" "model_deployment_query" {
  count                     = var.enable_model_deployments && local.query_model_is_distinct ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name                      = var.query_model.name
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.model_deployment_embedding,
  ]

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.query_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.query_model.name
        version = var.query_model.version
      }
    }
  }
}

resource "azapi_resource" "model_deployment_evaluation" {
  count                     = var.enable_model_deployments && local.eval_model_is_distinct ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name                      = var.evaluation_model.name
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.model_deployment_embedding,
    azapi_resource.model_deployment_query,
  ]

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.evaluation_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.evaluation_model.name
        version = var.evaluation_model.version
      }
    }
  }
}

# Project creation for Foundry frequently requires preview API support.
resource "azapi_resource" "foundry_project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview"
  name                      = "project-main"
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false
  response_export_values = [
    "identity.principalId",
  ]

  body = {
    location = var.location
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      description = "Primary project for hosted agent runtime"
    }
  }
}

# Required for secured standard-agent capability host operations.
resource "azurerm_role_assignment" "project_storage_account_contributor" {
  scope                            = var.storage_account_id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azapi_resource.foundry_project.output.identity.principalId
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_cosmosdb_operator" {
  scope                            = var.cosmosdb_account_id
  role_definition_name             = "Cosmos DB Operator"
  principal_id                     = azapi_resource.foundry_project.output.identity.principalId
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_search_service_contributor" {
  scope                            = var.search_service_id
  role_definition_name             = "Search Service Contributor"
  principal_id                     = azapi_resource.foundry_project.output.identity.principalId
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_search_index_data_contributor" {
  scope                            = var.search_service_id
  role_definition_name             = "Search Index Data Contributor"
  principal_id                     = azapi_resource.foundry_project.output.identity.principalId
  skip_service_principal_aad_check = true
}

resource "azapi_resource" "connection_storage" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = var.storage_account_name
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.foundry_project,
  ]

  body = {
    name = var.storage_account_name
    properties = {
      category = "AzureStorageAccount"
      target   = "https://${var.storage_account_name}.blob.core.windows.net/"
      authType = "AAD"
      metadata = {
        ApiType    = "Azure"
        ResourceId = var.storage_account_id
        location   = var.location
      }
    }
  }
}

resource "azapi_resource" "connection_cosmosdb" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = var.cosmosdb_account_name
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.foundry_project,
  ]

  body = {
    name = var.cosmosdb_account_name
    properties = {
      category = "CosmosDb"
      target   = "https://${var.cosmosdb_account_name}.documents.azure.com:443/"
      authType = "AAD"
      metadata = {
        ApiType    = "Azure"
        ResourceId = var.cosmosdb_account_id
        location   = var.location
      }
    }
  }
}

resource "azapi_resource" "connection_search" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = var.search_service_name
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.foundry_project,
  ]

  body = {
    name = var.search_service_name
    properties = {
      category = "CognitiveSearch"
      target   = "https://${var.search_service_name}.search.windows.net"
      authType = "AAD"
      metadata = {
        ApiType    = "Azure"
        ApiVersion = "2025-05-01-preview"
        ResourceId = var.search_service_id
        location   = var.location
      }
    }
  }
}

# Account-level capability host for private-network standard agents.
resource "azapi_resource" "account_capability_host" {
  type                      = "Microsoft.CognitiveServices/accounts/capabilityHosts@2025-04-01-preview"
  name                      = "accountcaphost"
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  timeouts {
    create = "60m"
    update = "90m"
    delete = "60m"
  }

  depends_on = [
    azapi_resource.model_deployment_embedding,
    azapi_resource.model_deployment_query,
    azapi_resource.model_deployment_evaluation,
  ]

  body = {
    properties = {
      capabilityHostKind = "Agents"
      customerSubnet     = var.delegated_agent_subnet_id
    }
  }
}

# Force destroy/create of project capability host when backing connection names
# change, as long-running in-place updates can hang in preview APIs.
resource "terraform_data" "project_capability_host_recreate" {
  input = {
    vector_store_connection   = var.search_service_name
    storage_connection        = var.storage_account_name
    thread_storage_connection = var.cosmosdb_account_name
  }
}

# Project capability host must reference exactly one connection per backing store.
resource "azapi_resource" "project_capability_host" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview"
  name                      = "caphostproj"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false

  timeouts {
    create = "60m"
    update = "90m"
    delete = "60m"
  }

  lifecycle {
    replace_triggered_by = [
      terraform_data.project_capability_host_recreate,
    ]
  }

  # If intermittent RBAC propagation failures return, add an explicit time_sleep
  # between role assignments and this resource.

  depends_on = [
    azapi_resource.account_capability_host,
    azapi_resource.connection_storage,
    azapi_resource.connection_cosmosdb,
    azapi_resource.connection_search,
    azurerm_role_assignment.project_storage_account_contributor,
    azurerm_role_assignment.project_cosmosdb_operator,
    azurerm_role_assignment.project_search_service_contributor,
    azurerm_role_assignment.project_search_index_data_contributor,
  ]

  body = {
    properties = {
      capabilityHostKind = "Agents"
      vectorStoreConnections = [
        var.search_service_name,
      ]
      storageConnections = [
        var.storage_account_name,
      ]
      threadStorageConnections = [
        var.cosmosdb_account_name,
      ]
    }
  }
}
