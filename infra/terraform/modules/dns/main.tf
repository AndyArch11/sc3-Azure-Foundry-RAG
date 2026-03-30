locals {
  private_dns_zones = [
    "privatelink.azurecr.io",
    "privatelink.blob.core.windows.net",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.documents.azure.com",
    "privatelink.file.core.windows.net",
    "privatelink.openai.azure.com",
    "privatelink.search.windows.net",
    "privatelink.services.ai.azure.com",
    "privatelink.vaultcore.azure.net"
  ]
}

resource "azurerm_private_dns_zone" "zones" {
  for_each            = toset(local.private_dns_zones)
  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "links" {
  for_each              = azurerm_private_dns_zone.zones
  name                  = "link-${replace(each.key, ".", "-")}"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = each.value.name
  virtual_network_id    = var.vnet_id
}
