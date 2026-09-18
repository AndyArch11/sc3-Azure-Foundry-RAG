resource "azurerm_api_management" "this" {
  name                = var.api_management_name
  location            = var.location
  resource_group_name = var.resource_group_name
  publisher_name      = var.publisher_name
  publisher_email     = var.publisher_email
  sku_name            = var.sku_name

  virtual_network_type = "External"

  virtual_network_configuration {
    subnet_id = var.virtual_network_subnet_id
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [public_network_access_enabled]
  }
}