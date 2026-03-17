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
  source              = "./modules/dns"
  resource_group_name = module.foundation.resource_group_name
  location            = var.location
  vnet_id             = module.network.vnet_id
  tags                = local.tags
}

module "observability" {
  source              = "./modules/observability"
  resource_group_name = module.foundation.resource_group_name
  location            = var.location
  workspace_name      = "law-${local.naming_suffix}"
  tags                = local.tags
}

module "data_services" {
  source              = "./modules/data_services"
  resource_group_name = module.foundation.resource_group_name
  location            = var.location
  suffix              = local.naming_suffix
  tags                = local.tags
}

module "foundry" {
  source                    = "./modules/foundry"
  resource_group_name       = module.foundation.resource_group_name
  location                  = var.location
  suffix                    = local.naming_suffix
  delegated_agent_subnet_id = module.network.agent_subnet_id
  storage_account_id        = module.data_services.storage_account_id
  storage_account_name      = module.data_services.storage_account_name
  search_service_id         = module.data_services.search_service_id
  search_service_name       = module.data_services.search_service_name
  cosmosdb_account_id       = module.data_services.cosmosdb_account_id
  cosmosdb_account_name     = module.data_services.cosmosdb_account_name
  embedding_model           = var.embedding_model
  query_model               = var.query_model
  evaluation_model          = var.evaluation_model
  enable_model_deployments  = var.enable_model_deployments
  tags                      = local.tags
}

module "private_endpoints" {
  source                     = "./modules/private_endpoints"
  resource_group_name        = module.foundation.resource_group_name
  location                   = var.location
  private_endpoint_subnet_id = module.network.private_endpoint_subnet_id
  private_dns_zone_ids       = module.dns.private_dns_zone_ids
  storage_account_id         = module.data_services.storage_account_id
  search_service_id          = module.data_services.search_service_id
  cosmosdb_account_id        = module.data_services.cosmosdb_account_id
  foundry_account_id         = module.foundry.foundry_account_id
  acr_id                     = module.data_services.acr_id
  tags                       = local.tags
}

module "identity" {
  source                         = "./modules/identity"
  resource_group_name            = module.foundation.resource_group_name
  location                       = var.location
  suffix                         = local.naming_suffix
  deployment_principal_object_id = data.azurerm_client_config.current.object_id
  search_service_principal_id    = module.data_services.search_service_principal_id
  scope_ids = {
    storage = module.data_services.storage_account_id
    search  = module.data_services.search_service_id
    cosmos  = module.data_services.cosmosdb_account_id
    foundry = module.foundry.foundry_account_id
    acr     = module.data_services.acr_id
  }
  tags = local.tags
}

module "bastion_jumpbox" {
  source                       = "./modules/bastion_jumpbox"
  resource_group_name          = module.foundation.resource_group_name
  location                     = var.location
  jumpbox_subnet_id            = module.network.jumpbox_subnet_id
  azure_bastion_subnet_id      = module.network.azure_bastion_subnet_id
  suffix                       = local.naming_suffix
  jumpbox_admin_ssh_public_key = local.use_key_vault_jumpbox_key ? data.azurerm_key_vault_secret.jumpbox_admin_ssh_public_key[0].value : var.jumpbox_admin_ssh_public_key
  jumpbox_vm_size              = var.jumpbox_vm_size
  agent_runtime_identity_id    = module.identity.agent_runtime_identity_id
  tags                         = local.tags
}

module "agent_hosting" {
  source                     = "./modules/agent_hosting"
  resource_group_name        = module.foundation.resource_group_name
  location                   = var.location
  suffix                     = local.naming_suffix
  delegated_agent_subnet_id  = module.network.container_apps_subnet_id
  log_analytics_workspace_id = module.observability.log_analytics_workspace_id
  acr_login_server           = module.data_services.acr_login_server
  agent_runtime_identity_id  = module.identity.agent_runtime_identity_id
  agent_runtime_client_id    = module.identity.agent_runtime_client_id
  azure_search_endpoint      = "https://${module.data_services.search_service_name}.search.windows.net"
  azure_openai_endpoint      = "https://${module.foundry.foundry_account_name}.openai.azure.com"
  storage_account_name       = module.data_services.storage_account_name
  storage_account_id         = module.data_services.storage_account_id
  embedding_deployment_name  = var.embedding_model.name
  embedding_dimensions       = 1536
  enable_ingestion_job       = var.enable_ingestion_job
  tags                       = local.tags
}

