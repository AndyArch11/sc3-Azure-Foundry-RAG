output "api_ids" {
  value = { for name, api in azurerm_api_management_api.this : name => api.id }
}

output "operation_ids" {
  value = { for name, operation in azurerm_api_management_api_operation.this : name => operation.id }
}