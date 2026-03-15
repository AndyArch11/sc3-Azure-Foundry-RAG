output "agent_runtime_identity_id" {
  value = azurerm_user_assigned_identity.agent_runtime.id
}

output "agent_runtime_principal_id" {
  value = azurerm_user_assigned_identity.agent_runtime.principal_id
}
