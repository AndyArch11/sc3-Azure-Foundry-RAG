module "foundation" {
  source              = "./modules/foundation"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.tags
}

data "azurerm_client_config" "current" {}

module "network" {
  source                       = "./modules/network"
  resource_group_name          = module.foundation.resource_group_name
  location                     = var.location
  vnet_name                    = "vnet-${local.naming_suffix}"
  vnet_cidr                    = var.vnet_cidr
  private_endpoint_subnet_cidr = var.private_endpoint_subnet_cidr
  agent_subnet_cidr            = var.agent_subnet_cidr
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
  tags                       = local.tags
}

module "identity" {
  source                         = "./modules/identity"
  resource_group_name            = module.foundation.resource_group_name
  location                       = var.location
  suffix                         = local.naming_suffix
  deployment_principal_object_id = data.azurerm_client_config.current.object_id
  scope_ids = {
    storage = module.data_services.storage_account_id
    search  = module.data_services.search_service_id
    cosmos  = module.data_services.cosmosdb_account_id
    foundry = module.foundry.foundry_account_id
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
  jumpbox_admin_ssh_public_key = var.jumpbox_admin_ssh_public_key
  jumpbox_vm_size              = var.jumpbox_vm_size
  tags                         = local.tags
}

