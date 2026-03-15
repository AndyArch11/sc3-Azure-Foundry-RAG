output "private_endpoint_ids" {
  value = { for k, pe in azurerm_private_endpoint.this : k => pe.id }
}
