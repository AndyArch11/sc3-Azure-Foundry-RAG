module "foundation" {
  source              = "./modules/foundation"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.tags
}

data "azurerm_client_config" "current" {}

locals {
  bootstrap_key_vault_resource_group_name = trimspace(var.bootstrap_key_vault_resource_group_name) != "" ? var.bootstrap_key_vault_resource_group_name : "rg-tfstate-${var.environment}"
  jumpbox_ssh_public_key_secret_name      = trimspace(var.jumpbox_ssh_public_key_secret_name) != "" ? var.jumpbox_ssh_public_key_secret_name : "jumpbox-admin-ssh-public-key-${var.environment}"
  use_key_vault_jumpbox_key               = (trimspace(var.jumpbox_admin_ssh_public_key) == "" || trimspace(var.jumpbox_admin_ssh_public_key) == "<set-me-ssh-public-key>") && trimspace(var.bootstrap_key_vault_name) != ""
  # BYOL (Bring-Your-Own-Network): use provided IDs if supplied, otherwise use module outputs.
  use_byol_network           = trimspace(var.byol_vnet_id) != ""
  vnet_id                    = local.use_byol_network ? var.byol_vnet_id : module.network[0].vnet_id
  container_apps_subnet_id   = local.use_byol_network ? var.byol_container_apps_subnet_id : module.network[0].container_apps_subnet_id
  private_endpoint_subnet_id = local.use_byol_network ? var.byol_private_endpoint_subnet_id : module.network[0].private_endpoint_subnet_id
  agent_subnet_id            = local.use_byol_network ? var.byol_agent_subnet_id : module.network[0].agent_subnet_id
  jumpbox_subnet_id          = local.use_byol_network ? var.byol_jumpbox_subnet_id : module.network[0].jumpbox_subnet_id
  azure_bastion_subnet_id    = local.use_byol_network ? var.byol_azure_bastion_subnet_id : module.network[0].azure_bastion_subnet_id
}

data "azurerm_key_vault" "bootstrap" {
  count               = local.use_key_vault_jumpbox_key ? 1 : 0
  name                = var.bootstrap_key_vault_name
  resource_group_name = local.bootstrap_key_vault_resource_group_name
}

data "azurerm_key_vault_secret" "jumpbox_admin_ssh_public_key" {
  count        = local.use_key_vault_jumpbox_key ? 1 : 0
  name         = local.jumpbox_ssh_public_key_secret_name
  key_vault_id = data.azurerm_key_vault.bootstrap[0].id
}

module "network" {
  count                        = local.use_byol_network ? 0 : 1
  source                       = "./modules/network"
  resource_group_name          = module.foundation.resource_group_name
  location                     = var.location
  vnet_name                    = "vnet-${local.naming_suffix}"
  vnet_cidr                    = var.vnet_cidr
  private_endpoint_subnet_cidr = var.private_endpoint_subnet_cidr
  agent_subnet_cidr            = var.agent_subnet_cidr
  container_apps_subnet_cidr   = var.container_apps_subnet_cidr
  jumpbox_subnet_cidr          = var.jumpbox_subnet_cidr
  azure_bastion_subnet_cidr    = var.azure_bastion_subnet_cidr
  tags                         = local.tags
}

module "dns" {
  count               = local.use_byol_network ? 0 : 1
  source              = "./modules/dns"
  resource_group_name = module.foundation.resource_group_name
  location            = var.location
  vnet_id             = local.vnet_id
  tags                = local.tags
}

module "observability" {
  source                          = "./modules/observability"
  resource_group_name             = module.foundation.resource_group_name
  location                        = var.location
  workspace_name                  = trimspace(var.log_analytics_workspace_name_override) != "" ? var.log_analytics_workspace_name_override : "law-${local.naming_suffix}"
  monitor_workspace_name_override = var.monitor_workspace_name_override
  tags                            = local.tags
}

module "data_services" {
  source                              = "./modules/data_services"
  resource_group_name                 = module.foundation.resource_group_name
  location                            = var.location
  suffix                              = local.naming_suffix
  search_service_name_override        = var.search_service_name_override
  storage_account_name_override       = var.storage_account_name_override
  acr_name_override                   = var.acr_name_override
  cosmos_account_name_override        = var.cosmos_account_name_override
  cosmos_database_name                = var.cosmos_database_name
  cosmos_container_name               = var.cosmos_container_name
  cosmos_orchestration_container_name = var.cosmos_orchestration_container_name
  tags                                = local.tags
}

module "foundry" {
  source                                    = "./modules/foundry"
  resource_group_name                       = module.foundation.resource_group_name
  location                                  = var.location
  suffix                                    = local.naming_suffix
  foundry_account_name_override             = var.foundry_account_name_override
  delegated_agent_subnet_id                 = local.agent_subnet_id
  storage_account_id                        = module.data_services.storage_account_id
  storage_account_name                      = module.data_services.storage_account_name
  search_service_id                         = module.data_services.search_service_id
  search_service_name                       = module.data_services.search_service_name
  cosmosdb_account_id                       = module.data_services.cosmosdb_account_id
  cosmosdb_account_name                     = module.data_services.cosmosdb_account_name
  embedding_model                           = var.embedding_model
  query_model                               = var.query_model
  evaluation_model                          = var.evaluation_model
  validator_model                           = var.validator_model
  enable_model_deployments                  = var.enable_model_deployments
  foundry_network_acl_bypass_azure_services = var.foundry_network_acl_bypass_azure_services
  tags                                      = local.tags
}

resource "azurerm_search_shared_private_link_service" "search_to_foundry_account" {
  name               = "probe-foundry"
  search_service_id  = module.data_services.search_service_id
  target_resource_id = module.foundry.foundry_account_id
  subresource_name   = "foundry_account"
  request_message    = "probe"
}

# Private path for Search indexer/data-source access to Blob Storage.
# This avoids relying on trusted-services bypass for Search -> Storage traffic.
resource "azurerm_search_shared_private_link_service" "search_to_storage_blob" {
  name               = "datasource-storage-blob"
  search_service_id  = module.data_services.search_service_id
  target_resource_id = module.data_services.storage_account_id
  subresource_name   = "blob"
  request_message    = "Search indexer private access to storage blob datasource"
}

module "private_endpoints" {
  source                     = "./modules/private_endpoints"
  resource_group_name        = module.foundation.resource_group_name
  location                   = var.location
  private_endpoint_subnet_id = local.private_endpoint_subnet_id
  private_dns_zone_ids       = local.use_byol_network ? {} : module.dns[0].private_dns_zone_ids
  storage_account_id         = module.data_services.storage_account_id
  search_service_id          = module.data_services.search_service_id
  cosmosdb_account_id        = module.data_services.cosmosdb_account_id
  foundry_account_id         = module.foundry.foundry_account_id
  acr_id                     = module.data_services.acr_id
  tags                       = local.tags
}

module "identity" {
  source                               = "./modules/identity"
  resource_group_name                  = module.foundation.resource_group_name
  location                             = var.location
  suffix                               = local.naming_suffix
  agent_runtime_identity_name_override = var.agent_runtime_identity_name_override
  deployment_principal_object_id       = data.azurerm_client_config.current.object_id
  search_service_principal_id          = module.data_services.search_service_principal_id
  cosmos_database_name                 = var.cosmos_database_name
  cosmos_container_name                = var.cosmos_container_name
  cosmos_orchestration_container_name  = var.cosmos_orchestration_container_name
  scope_ids = {
    storage       = module.data_services.storage_account_id
    search        = module.data_services.search_service_id
    cosmos        = module.data_services.cosmosdb_account_id
    foundry       = module.foundry.foundry_account_id
    acr           = module.data_services.acr_id
    log_analytics = module.observability.log_analytics_workspace_id
  }
  tags = local.tags
}

module "app_secrets" {
  count                      = (!local.use_byol_network && var.enable_query_web_app) ? 1 : 0
  source                     = "./modules/app_secrets"
  resource_group_name        = module.foundation.resource_group_name
  location                   = var.location
  suffix                     = local.naming_suffix
  private_endpoint_subnet_id = local.private_endpoint_subnet_id
  private_dns_zone_id        = module.dns[0].private_dns_zone_ids["privatelink.vaultcore.azure.net"]
  agent_runtime_principal_id = module.identity.agent_runtime_principal_id
  tags                       = local.tags
}

module "bastion_jumpbox" {
  count                        = local.use_byol_network ? 0 : 1
  source                       = "./modules/bastion_jumpbox"
  depends_on                   = [module.network]
  resource_group_name          = module.foundation.resource_group_name
  location                     = var.location
  jumpbox_subnet_id            = local.jumpbox_subnet_id
  azure_bastion_subnet_id      = local.azure_bastion_subnet_id
  suffix                       = local.naming_suffix
  jumpbox_admin_ssh_public_key = local.use_key_vault_jumpbox_key ? data.azurerm_key_vault_secret.jumpbox_admin_ssh_public_key[0].value : var.jumpbox_admin_ssh_public_key
  jumpbox_vm_size              = var.jumpbox_vm_size
  agent_runtime_identity_id    = module.identity.agent_runtime_identity_id
  tags                         = local.tags
}

# --- Entra app registration for query_web EasyAuth ---
# Created automatically when enable_query_web_app = true so operators don't
# need to pre-create an app registration manually.
#
# Client secret creation/rotation is intentionally handled outside Terraform
# (for example from jumpbox), then stored in a private Key Vault. Terraform
# receives only a Key Vault secret ID and never the secret value.

resource "azuread_application" "query_web" {
  count        = var.enable_query_web_app ? 1 : 0
  display_name = "app-rag-query-${local.naming_suffix}"

  sign_in_audience        = "AzureADMyOrg"
  group_membership_claims = ["SecurityGroup"]

  web {
    # Redirect URIs are managed by azuread_application_redirect_uris below
    # (depends on the Container App FQDN that is only known after apply).
    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled     = true
    }
  }
}

# Set the EasyAuth callback URI after the Container App FQDN is known.
resource "azuread_application_redirect_uris" "query_web" {
  count          = var.enable_query_web_app ? 1 : 0
  application_id = azuread_application.query_web[0].id
  type           = "Web"
  redirect_uris  = ["https://${module.agent_hosting.query_web_fqdn}/.auth/login/aad/callback"]
}

module "agent_hosting" {
  source                                            = "./modules/agent_hosting"
  resource_group_name                               = module.foundation.resource_group_name
  location                                          = var.location
  suffix                                            = local.naming_suffix
  delegated_agent_subnet_id                         = local.container_apps_subnet_id
  vnet_id                                           = local.vnet_id
  log_analytics_workspace_id                        = module.observability.log_analytics_workspace_id
  azure_monitor_data_collection_rule_id             = module.observability.monitor_data_collection_rule_id
  azure_monitor_data_collection_endpoint_id         = module.observability.monitor_data_collection_endpoint_id
  acr_login_server                                  = module.data_services.acr_login_server
  agent_runtime_identity_id                         = module.identity.agent_runtime_identity_id
  agent_runtime_client_id                           = module.identity.agent_runtime_client_id
  agent_runtime_principal_id                        = module.identity.agent_runtime_principal_id
  azure_search_endpoint                             = "https://${module.data_services.search_service_name}.search.windows.net"
  azure_openai_endpoint                             = "https://${module.foundry.foundry_account_name}.openai.azure.com"
  ai_services_endpoint                              = "https://${module.foundry.foundry_account_name}.cognitiveservices.azure.com"
  azure_cosmos_endpoint                             = "https://${module.data_services.cosmosdb_account_name}.documents.azure.com:443/"
  cosmos_database_name                              = var.cosmos_database_name
  cosmos_container_name                             = var.cosmos_container_name
  cosmos_orchestration_container_name               = var.cosmos_orchestration_container_name
  storage_account_name                              = module.data_services.storage_account_name
  storage_account_id                                = module.data_services.storage_account_id
  search_index_name                                 = var.search_index_name
  controls_index_name                               = var.controls_index_name
  embedding_deployment_name                         = var.embedding_model.name
  query_deployment_name                             = var.query_model.name
  evaluator_deployment_name                         = var.evaluation_model.name
  embedding_dimensions                              = 1536
  query_top_k                                       = var.query_top_k
  query_default_temperature                         = var.query_default_temperature
  query_evaluator_temperature                       = var.query_evaluator_temperature
  query_eval_threshold                              = var.query_eval_threshold
  control_llm_review_enabled                        = var.control_llm_review_enabled
  control_llm_review_heuristic_threshold            = var.control_llm_review_heuristic_threshold
  prompt_injection_validator_enabled                = var.prompt_injection_validator_enabled
  prompt_injection_validator_deployment             = trimspace(var.prompt_injection_validator_deployment) != "" ? var.prompt_injection_validator_deployment : var.validator_model.name
  prompt_injection_validator_threshold              = var.prompt_injection_validator_threshold
  prompt_injection_validator_temperature            = var.prompt_injection_validator_temperature
  prompt_injection_validator_timeout_s              = var.prompt_injection_validator_timeout_s
  prompt_injection_validator_mode                   = var.prompt_injection_validator_mode
  guardrail_metrics_in_response                     = var.guardrail_metrics_in_response
  query_web_entra_client_secret_key_vault_secret_id = var.query_web_entra_client_secret_key_vault_secret_id
  ingestion_job_image_tag                           = var.ingestion_job_image_tag
  query_web_auth_token                              = var.query_web_auth_token
  query_web_required_group_object_id                = var.query_web_required_group_object_id
  query_web_entra_client_id                         = try(azuread_application.query_web[0].client_id, "")
  entra_tenant_id                                   = data.azurerm_client_config.current.tenant_id
  subscription_id                                   = var.subscription_id
  query_web_image_tag                               = var.query_web_image_tag
  enable_ingestion_job                              = var.enable_ingestion_job
  enable_query_web_app                              = var.enable_query_web_app
  enable_confluence_poller_app                      = var.enable_confluence_poller_app
  confluence_poller_image_tag                       = var.confluence_poller_image_tag
  confluence_base_url                               = var.confluence_base_url
  confluence_auth_mode                              = var.confluence_auth_mode
  confluence_auth_email                             = var.confluence_auth_email
  confluence_api_token                              = var.confluence_api_token
  confluence_cloud_id                               = var.confluence_cloud_id
  confluence_account_id                             = var.confluence_account_id
  confluence_mention_aliases                        = var.confluence_mention_aliases
  confluence_poll_space_keys                        = var.confluence_poll_space_keys
  confluence_poll_interval_seconds                  = var.confluence_poll_interval_seconds
  confluence_poll_lease_ttl_seconds                 = var.confluence_poll_lease_ttl_seconds
  confluence_poll_max_event_attempts                = var.confluence_poll_max_event_attempts
  confluence_poll_initial_lookback                  = var.confluence_poll_initial_lookback
  confluence_poll_dry_run                           = var.confluence_poll_dry_run
  query_web_public_endpoint                         = var.query_web_public_endpoint
  tags                                              = local.tags
}

