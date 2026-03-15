resource "random_string" "storage_suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_storage_account" "this" {
  name                            = substr(replace("st${var.suffix}${random_string.storage_suffix.result}", "-", ""), 0, 24)
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  tags                            = var.tags
}

resource "azurerm_storage_container" "grounding_data" {
  name                  = "grounding-data"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_search_service" "this" {
  name                          = "srch-${var.suffix}"
  resource_group_name           = var.resource_group_name
  location                      = var.location
  sku                           = "standard"
  public_network_access_enabled = false
  local_authentication_enabled  = false
  tags                          = var.tags
}

resource "azurerm_cosmosdb_account" "this" {
  name                          = "cosmos-${var.suffix}"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  offer_type                    = "Standard"
  kind                          = "GlobalDocumentDB"
  public_network_access_enabled = false

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.location
    failover_priority = 0
  }

  capabilities {
    name = "EnableServerless"
  }

  tags = var.tags
}
