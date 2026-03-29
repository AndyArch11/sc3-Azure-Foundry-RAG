variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "search_service_name_override" {
  type        = string
  description = "Optional explicit Azure AI Search service name override. Leave empty to use srch-<suffix>."
  default     = ""
}
variable "cosmos_database_name" { type = string }
variable "cosmos_container_name" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
