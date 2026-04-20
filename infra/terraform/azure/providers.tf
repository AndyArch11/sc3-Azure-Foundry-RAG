provider "azurerm" {
  features {}
  # Use Azure AD authentication for storage operations instead of account keys.
  # This allows Terraform to work with storage accounts that have key-based auth disabled.
  storage_use_azuread = true
}

provider "azapi" {}

provider "azuread" {}
