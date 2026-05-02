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

resource "azapi_resource" "cae_prometheus_scrape" {
  count                     = var.azure_monitor_data_collection_rule_id != "" ? 1 : 0
  type                      = "Microsoft.App/managedEnvironments/prometheusConfiguration@2024-08-01"
  name                      = "default"
  parent_id                 = azurerm_container_app_environment.this.id
  schema_validation_enabled = false

  body = {
    properties = {
      enabled = true
    }
  }
}

resource "azurerm_monitor_data_collection_rule_association" "cae" {
  count                   = var.azure_monitor_data_collection_rule_id != "" ? 1 : 0
  name                    = "dcra-cae-prometheus"
  target_resource_id      = azurerm_container_app_environment.this.id
  data_collection_rule_id = var.azure_monitor_data_collection_rule_id

  depends_on = [azapi_resource.cae_prometheus_scrape]
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
        name  = "AI_SERVICES_ENDPOINT"
        value = var.ai_services_endpoint
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
      image  = "${var.acr_login_server}/query-web:${var.query_web_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
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
        name  = "AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME"
        value = var.cosmos_orchestration_container_name
      }
      env {
        name  = "AZURE_STORAGE_ACCOUNT_NAME"
        value = var.storage_account_name
      }
      env {
        name  = "AZURE_STORAGE_CONTAINER_NAME"
        value = "grounding-data"
      }
      env {
        name  = "AZURE_SEARCH_INDEX_NAME"
        value = var.search_index_name
      }
      env {
        name  = "AZURE_SEARCH_CONTROLS_INDEX_NAME"
        value = var.controls_index_name
      }
      env {
        name  = "INGESTION_JOB_SUBSCRIPTION_ID"
        value = var.subscription_id
      }
      env {
        name  = "INGESTION_JOB_RESOURCE_GROUP"
        value = var.resource_group_name
      }
      env {
        name  = "INGESTION_JOB_NAME"
        value = "caj-ingestion-${var.suffix}"
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
        name  = "EVALUATOR_TEMPERATURE"
        value = tostring(var.query_evaluator_temperature)
      }
      env {
        name  = "ACCEPTABLE_SCORE_THRESHOLD"
        value = tostring(var.query_eval_threshold)
      }
      env {
        name  = "PROMPT_INJECTION_VALIDATOR_ENABLED"
        value = tostring(var.prompt_injection_validator_enabled)
      }
      env {
        name  = "PROMPT_INJECTION_VALIDATOR_DEPLOYMENT"
        value = var.prompt_injection_validator_deployment
      }
      env {
        name  = "PROMPT_INJECTION_VALIDATOR_THRESHOLD"
        value = tostring(var.prompt_injection_validator_threshold)
      }
      env {
        name  = "PROMPT_INJECTION_VALIDATOR_TEMPERATURE"
        value = tostring(var.prompt_injection_validator_temperature)
      }
      env {
        name  = "PROMPT_INJECTION_VALIDATOR_TIMEOUT_S"
        value = tostring(var.prompt_injection_validator_timeout_s)
      }
      env {
        name  = "PROMPT_INJECTION_VALIDATOR_MODE"
        value = var.prompt_injection_validator_mode
      }
      env {
        name  = "GUARDRAIL_METRICS_IN_RESPONSE"
        value = tostring(var.guardrail_metrics_in_response)
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

resource "azurerm_container_app" "confluence_poller" {
  count                        = var.enable_confluence_poller_app ? 1 : 0
  name                         = "ca-conf-poller-${var.suffix}"
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
    for_each = var.confluence_api_token != "" ? [1] : []
    content {
      name  = "confluence-api-token"
      value = var.confluence_api_token
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "confluence-poller"
      image  = "${var.acr_login_server}/confluence-poller:${var.confluence_poller_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
      command = [
        "python",
        "-m",
        "runtime.assessment_orchestration.polling_worker_main",
      ]

      env {
        name  = "AZURE_CLIENT_ID"
        value = var.agent_runtime_client_id
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
        name  = "AZURE_COSMOS_ORCHESTRATION_CONTAINER_NAME"
        value = var.cosmos_orchestration_container_name
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
        name  = "AZURE_SEARCH_INDEX_NAME"
        value = var.search_index_name
      }
      env {
        name  = "AZURE_SEARCH_CONTROLS_INDEX_NAME"
        value = var.controls_index_name
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
        name  = "CONTROL_LLM_REVIEW_ENABLED"
        value = tostring(var.control_llm_review_enabled)
      }
      env {
        name  = "CONTROL_LLM_REVIEW_HEURISTIC_THRESHOLD"
        value = tostring(var.control_llm_review_heuristic_threshold)
      }
      env {
        name  = "CONFLUENCE_BASE_URL"
        value = var.confluence_base_url
      }
      env {
        name  = "CONFLUENCE_AUTH_MODE"
        value = var.confluence_auth_mode
      }
      env {
        name  = "CONFLUENCE_AUTH_EMAIL"
        value = var.confluence_auth_email
      }
      dynamic "env" {
        for_each = var.confluence_api_token != "" ? [1] : []
        content {
          name        = "CONFLUENCE_API_TOKEN"
          secret_name = "confluence-api-token"
        }
      }
      dynamic "env" {
        for_each = trimspace(var.confluence_cloud_id) != "" ? [1] : []
        content {
          name  = "CONFLUENCE_CLOUD_ID"
          value = var.confluence_cloud_id
        }
      }
      dynamic "env" {
        for_each = trimspace(var.confluence_account_id) != "" ? [1] : []
        content {
          name  = "CONFLUENCE_ACCOUNT_ID"
          value = var.confluence_account_id
        }
      }
      dynamic "env" {
        for_each = length(var.confluence_poll_space_keys) > 0 ? [1] : []
        content {
          name  = "CONFLUENCE_POLL_SPACE_KEYS"
          value = join(",", var.confluence_poll_space_keys)
        }
      }
      env {
        name  = "CONFLUENCE_MENTION_ALIASES"
        value = join(",", var.confluence_mention_aliases)
      }
      env {
        name  = "CONFLUENCE_POLL_INTERVAL_SECONDS"
        value = tostring(var.confluence_poll_interval_seconds)
      }
      env {
        name  = "CONFLUENCE_POLL_LEASE_TTL_SECONDS"
        value = tostring(var.confluence_poll_lease_ttl_seconds)
      }
      env {
        name  = "CONFLUENCE_POLL_MAX_EVENT_ATTEMPTS"
        value = tostring(var.confluence_poll_max_event_attempts)
      }
      env {
        name  = "CONFLUENCE_POLL_INITIAL_LOOKBACK"
        value = var.confluence_poll_initial_lookback
      }
      env {
        name  = "CONFLUENCE_POLL_DRY_RUN"
        value = tostring(var.confluence_poll_dry_run)
      }
    }
  }

  tags = var.tags
}

resource "azurerm_role_assignment" "confluence_poller_contributor" {
  count                = var.enable_confluence_poller_app ? 1 : 0
  scope                = azurerm_container_app.confluence_poller[0].id
  role_definition_name = "Contributor"
  principal_id         = var.agent_runtime_principal_id
}

resource "azurerm_role_assignment" "query_web_contributor" {
  count                = var.enable_query_web_app ? 1 : 0
  scope                = azurerm_container_app.query_web[0].id
  role_definition_name = "Contributor"
  principal_id         = var.agent_runtime_principal_id
}

resource "azurerm_monitor_diagnostic_setting" "ingestion" {
  count                      = var.enable_ingestion_job ? 1 : 0
  name                       = "diag-ingestion"
  target_resource_id         = azurerm_container_app_job.ingestion[0].id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

resource "azurerm_monitor_diagnostic_setting" "query_web" {
  count                      = var.enable_query_web_app ? 1 : 0
  name                       = "diag-query-web"
  target_resource_id         = azurerm_container_app.query_web[0].id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

resource "azurerm_monitor_diagnostic_setting" "confluence_poller" {
  count                      = var.enable_confluence_poller_app ? 1 : 0
  name                       = "diag-confluence-poller"
  target_resource_id         = azurerm_container_app.confluence_poller[0].id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
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
