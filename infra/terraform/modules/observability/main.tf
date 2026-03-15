resource "azurerm_log_analytics_workspace" "this" {
  name                = var.workspace_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_monitor_workspace" "prometheus" {
  name                = "amw-${replace(var.workspace_name, "law-", "")}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}
