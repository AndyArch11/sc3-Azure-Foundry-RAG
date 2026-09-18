locals {
  api_management_default_query_web_service_url = try(module.agent_hosting.query_web_fqdn, null) != null ? "https://${module.agent_hosting.query_web_fqdn}" : null
}

module "api_management_endpoints" {
  source = "./modules/api_management_endpoints"

  resource_group_name = module.foundation.resource_group_name
  api_management_name = module.api_management.name
  endpoints = {
    for endpoint_name, endpoint in var.mcp_endpoints : endpoint_name => {
      display_name          = endpoint.display_name
      path                  = endpoint.path
      service_url           = coalesce(try(endpoint.service_url, null), local.api_management_default_query_web_service_url)
      description           = try(endpoint.description, null)
      api_type              = try(endpoint.api_type, "http")
      subscription_required = endpoint.subscription_required
      revision              = endpoint.revision
      protocols             = endpoint.protocols
      operations = { for operation_name, operation in try(endpoint.operations, {}) : operation_name => {
        operation_id = operation.operation_id
        display_name = operation.display_name
        method       = operation.method
        url_template = operation.url_template
        description  = try(operation.description, null)
      } }
    }
  }
}