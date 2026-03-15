output "vnet_id" {
  value = azurerm_virtual_network.this.id
}

output "private_endpoint_subnet_id" {
  value = azurerm_subnet.private_endpoints.id
}

output "agent_subnet_id" {
  value = azurerm_subnet.agent.id
}

output "jumpbox_subnet_id" {
  value = azurerm_subnet.jumpbox.id
}

output "azure_bastion_subnet_id" {
  value = azurerm_subnet.azure_bastion.id
}
