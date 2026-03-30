# Container App Environment — VNet-integrated on the delegated agent subnet.
# Workload Profiles mode enables the Consumption profile (serverless, pay-per-use).
resource "azurerm_container_app_environment" "this" {
  name                               = "cae-${var.suffix}"
  location                           = var.location
  resource_group_name                = var.resource_group_name
  infrastructure_resource_group_name = "ME_cae-${var.suffix}_${var.resource_group_name}_${var.location}"
  log_analytics_workspace_id         = var.log_analytics_workspace_id
  infrastructure_subnet_id           = var.delegated_agent_subnet_id
  internal_load_balancer_enabled     = !var.query_web_public_endpoint
  tags                               = var.tags

  # CREATION-LEVEL: changing query_web_public_endpoint re-creates this environment.
  lifecycle {
    ignore_changes = []
  }

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# Private DNS zone is only required for internal (VNet-only) environments.
# Public environments resolve via Azure-managed public DNS.
resource "azurerm_private_dns_zone" "container_apps" {
  count               = var.query_web_public_endpoint ? 0 : 1
  name                = azurerm_container_app_environment.this.default_domain
  resource_group_name = var.resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "container_apps" {
  count                 = var.query_web_public_endpoint ? 0 : 1
  name                  = "link-cae-to-vnet"
  resource_group_name   = azurerm_private_dns_zone.container_apps[0].resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.container_apps[0].name
  virtual_network_id    = var.vnet_id
}

# Container Apps internal load balancer IP (fixed for internal ingress deployments).
locals {
  cae_internal_lb_ip = "10.20.5.176"
}

# DNS A records for internal Container Apps — point to the CAE's internal load balancer.
# Not needed when the environment has a public endpoint.
resource "azurerm_private_dns_a_record" "ingestion_job" {
  count               = var.query_web_public_endpoint ? 0 : 1
  name                = "caj-ingestion-${var.suffix}.internal"
  zone_name           = azurerm_private_dns_zone.container_apps[0].name
  resource_group_name = azurerm_private_dns_zone.container_apps[0].resource_group_name
  ttl                 = 300
  records             = [local.cae_internal_lb_ip]
}

resource "azurerm_private_dns_a_record" "query_web" {
  count               = (var.enable_query_web_app && !var.query_web_public_endpoint) ? 1 : 0
  name                = "ca-rag-query-${var.suffix}.internal"
  zone_name           = azurerm_private_dns_zone.container_apps[0].name
  resource_group_name = azurerm_private_dns_zone.container_apps[0].resource_group_name
  ttl                 = 300
  records             = [local.cae_internal_lb_ip]
}

# VNet-scoped ingress hostname (external=true on an internal CAE).
resource "azurerm_private_dns_a_record" "query_web_vnet" {
  count               = (var.enable_query_web_app && !var.query_web_public_endpoint) ? 1 : 0
  name                = "ca-rag-query-${var.suffix}"
  zone_name           = azurerm_private_dns_zone.container_apps[0].name
  resource_group_name = azurerm_private_dns_zone.container_apps[0].resource_group_name
  ttl                 = 300
  records             = [local.cae_internal_lb_ip]
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
      image  = "${var.acr_login_server}/ingestion-runner:${var.ingestion_job_image_tag}"
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

resource "azurerm_container_app" "query_web" {
  count                        = var.enable_query_web_app ? 1 : 0
  name                         = "ca-rag-query-${var.suffix}"
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [var.agent_runtime_identity_id]
  }

  registry {
    server   = var.acr_login_server
    identity = var.agent_runtime_identity_id
  }

  dynamic "secret" {
    for_each = var.query_web_auth_token != "" ? [1] : []
    content {
      name  = "query-web-auth-token"
      value = var.query_web_auth_token
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1

    container {
      name   = "rag-query-web"
      image  = "${var.acr_login_server}/rag-query-web:${var.query_web_image_tag}"
      cpu    = 1.0
      memory = "2Gi"

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
        name  = "AZURE_COSMOS_ENDPOINT"
        value = var.azure_cosmos_endpoint
      }
      env {
        name  = "AZURE_COSMOS_DATABASE_NAME"
        value = var.cosmos_database_name
      }
      env {
        name  = "AZURE_COSMOS_CONTAINER_NAME"
        value = var.cosmos_container_name
      }
      env {
        name  = "AZURE_SEARCH_INDEX_NAME"
        value = var.search_index_name
      }
      env {
        name  = "EMBEDDING_DEPLOYMENT_NAME"
        value = var.embedding_deployment_name
      }
      env {
        name  = "QUERY_DEPLOYMENT_NAME"
        value = var.query_deployment_name
      }
      env {
        name  = "EVALUATOR_DEPLOYMENT_NAME"
        value = var.evaluator_deployment_name
      }
      env {
        name  = "SEARCH_TOP_K"
        value = tostring(var.query_top_k)
      }
      env {
        name  = "DEFAULT_TEMPERATURE"
        value = tostring(var.query_default_temperature)
      }
      env {
        name  = "ACCEPTABLE_SCORE_THRESHOLD"
        value = tostring(var.query_eval_threshold)
      }
      dynamic "env" {
        for_each = var.query_web_auth_token != "" ? [1] : []
        content {
          name        = "QUERY_WEB_AUTH_TOKEN"
          secret_name = "query-web-auth-token"
        }
      }
      dynamic "env" {
        for_each = var.query_web_required_group_object_id != "" ? [1] : []
        content {
          name  = "QUERY_WEB_REQUIRED_GROUP_OBJECT_ID"
          value = var.query_web_required_group_object_id
        }
      }
    }
  }

  # Secret for Container App EasyAuth via Key Vault reference.
  dynamic "secret" {
    for_each = var.query_web_entra_client_secret_key_vault_secret_id != "" ? [1] : []
    content {
      name                = "entra-auth-client-secret"
      key_vault_secret_id = var.query_web_entra_client_secret_key_vault_secret_id
      identity            = var.agent_runtime_identity_id
    }
  }

  tags = var.tags
}

resource "azurerm_role_assignment" "query_web_contributor" {
  count                = var.enable_query_web_app ? 1 : 0
  scope                = azurerm_container_app.query_web[0].id
  role_definition_name = "Contributor"
  principal_id         = var.agent_runtime_principal_id
}

# Container App built-in authentication (EasyAuth) — injects x-ms-client-principal headers
# so the app can gate access by Entra ID group membership without implementing OAuth itself.
# Only provisioned when both query_web_entra_client_id and
# query_web_entra_client_secret_key_vault_secret_id are set.
# Requires an Entra ID app registration with:
#   - Redirect URI set to https://<query-web-fqdn>/.auth/login/aad/callback
#   - Group membership claims enabled (groupMembershipClaims: SecurityGroup in the manifest)
resource "azapi_resource" "query_web_auth" {
  count     = (var.enable_query_web_app && var.query_web_entra_client_id != "" && var.query_web_entra_client_secret_key_vault_secret_id != "") ? 1 : 0
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.query_web[0].id

  body = {
    properties = {
      platform = {
        enabled        = true
        runtimeVersion = "~1"
      }
      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        excludedPaths               = ["/health"]
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = var.query_web_entra_client_id
            clientSecretSettingName = "entra-auth-client-secret"
            openIdIssuer            = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"
          }
          validation = {
            allowedAudiences = ["api://${var.query_web_entra_client_id}"]
          }
        }
      }
      login = {
        preserveUrlFragmentsForLogins = false
        tokenStore                    = { enabled = false }
      }
    }
  }
}
