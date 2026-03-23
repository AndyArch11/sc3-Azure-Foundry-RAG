# State address migrations generated when network and dns modules gained count
# (to support bring-your-own-network). These blocks prevent Terraform from
# destroying and recreating existing resources when module addresses change.
# Safe to keep indefinitely; they are no-ops once state has been migrated.

moved {
  from = module.network.azurerm_virtual_network.this
  to   = module.network[0].azurerm_virtual_network.this
}

moved {
  from = module.network.azurerm_subnet.agent
  to   = module.network[0].azurerm_subnet.agent
}

moved {
  from = module.network.azurerm_subnet.private_endpoints
  to   = module.network[0].azurerm_subnet.private_endpoints
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.azurecr.io"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.azurecr.io"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.blob.core.windows.net"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.blob.core.windows.net"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.cognitiveservices.azure.com"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.cognitiveservices.azure.com"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.documents.azure.com"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.documents.azure.com"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.file.core.windows.net"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.file.core.windows.net"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.openai.azure.com"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.openai.azure.com"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.search.windows.net"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.search.windows.net"]
}

moved {
  from = module.dns.azurerm_private_dns_zone.zones["privatelink.services.ai.azure.com"]
  to   = module.dns[0].azurerm_private_dns_zone.zones["privatelink.services.ai.azure.com"]
}
