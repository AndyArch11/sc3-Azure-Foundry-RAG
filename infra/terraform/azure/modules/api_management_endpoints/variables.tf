variable "resource_group_name" { type = string }
variable "api_management_name" { type = string }

variable "endpoints" {
  type = map(object({
    display_name           = string
    path                   = string
    service_url            = string
    description            = optional(string)
    api_type               = optional(string, "http")
    subscription_required  = optional(bool, false)
    revision               = optional(string, "1")
    protocols              = optional(list(string), ["https"])
    openapi_spec_path      = optional(string)
    openapi_content_format = optional(string, "openapi")
    operations = optional(map(object({
      operation_id = string
      display_name = string
      method       = string
      url_template = string
      description  = optional(string)
    })), {})
  }))
  default = {}
}