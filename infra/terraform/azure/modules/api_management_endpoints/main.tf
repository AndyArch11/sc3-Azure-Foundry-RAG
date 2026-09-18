locals {
  operation_entries = flatten([
    for endpoint_name, endpoint in var.endpoints : [
      for operation_name, operation in try(endpoint.operations, {}) : {
        key           = "${endpoint_name}.${operation_name}"
        endpoint_name = endpoint_name
        operation     = operation
      }
    ]
  ])

  operations = { for entry in local.operation_entries : entry.key => entry }
}

resource "azurerm_api_management_api" "this" {
  for_each = var.endpoints

  name                  = each.key
  api_management_name   = var.api_management_name
  resource_group_name   = var.resource_group_name
  revision              = each.value.revision
  display_name          = each.value.display_name
  path                  = each.value.path
  protocols             = each.value.protocols
  api_type              = each.value.api_type
  description           = try(each.value.description, null)
  service_url           = each.value.service_url
  subscription_required = each.value.subscription_required

  dynamic "import" {
    for_each = try(each.value.openapi_spec_path, null) != null ? [1] : []

    content {
      content_format = each.value.openapi_content_format
      content_value  = file(each.value.openapi_spec_path)
    }
  }
}

resource "azurerm_api_management_api_operation" "this" {
  for_each = local.operations

  operation_id        = each.value.operation.operation_id
  api_name            = azurerm_api_management_api.this[each.value.endpoint_name].name
  api_management_name = var.api_management_name
  resource_group_name = var.resource_group_name
  display_name        = each.value.operation.display_name
  method              = each.value.operation.method
  url_template        = each.value.operation.url_template
  description         = try(each.value.operation.description, null)
}