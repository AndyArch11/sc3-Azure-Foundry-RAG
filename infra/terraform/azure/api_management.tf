module "api_management" {
  source = "./modules/api_management"

  resource_group_name       = module.foundation.resource_group_name
  location                  = var.location
  api_management_name       = trimspace(var.api_management_name_override) != "" ? var.api_management_name_override : "apim-${local.naming_suffix}"
  publisher_name            = var.api_management_publisher_name
  publisher_email           = var.api_management_publisher_email
  sku_name                  = "StandardV2_1"
  virtual_network_subnet_id = local.api_management_subnet_id
  tags                      = local.tags
}

resource "azapi_update_resource" "api_management_disable_public_access" {
  type        = "Microsoft.ApiManagement/service@2024-05-01"
  resource_id = module.api_management.id
  body = jsonencode({
    properties = {
      publicNetworkAccess = "Disabled"
    }
  })

  depends_on = [module.private_endpoints]
}