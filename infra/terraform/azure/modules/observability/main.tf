resource "azurerm_log_analytics_workspace" "this" {
  name                = var.workspace_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_monitor_workspace" "prometheus" {
  name                = trimspace(var.monitor_workspace_name_override) != "" ? var.monitor_workspace_name_override : "amw-${replace(var.workspace_name, "law-", "")}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_monitor_data_collection_endpoint" "this" {
  name                = "dce-${replace(var.workspace_name, "law-", "")}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_monitor_data_collection_rule" "prometheus" {
  name                = "dcr-prometheus-${replace(var.workspace_name, "law-", "")}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.this.id

  destinations {
    monitor_account {
      monitor_account_id = azurerm_monitor_workspace.prometheus.id
      name               = "amw-destination"
    }
  }

  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["amw-destination"]
  }

  data_sources {
    prometheus_forwarder {
      name    = "PrometheusDataSource"
      streams = ["Microsoft-PrometheusMetrics"]
    }
  }
}
