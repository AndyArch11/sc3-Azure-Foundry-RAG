variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "agent_runtime_identity_name_override" {
  type        = string
  description = "Optional explicit runtime UAMI name override. Leave empty to use id-agent-runtime-<suffix>."
  default     = ""
}
variable "scope_ids" { type = map(string) }
variable "deployment_principal_object_id" {
  type        = string
  description = "Object ID of the principal running Terraform; assigned Azure AI Project Manager on Foundry."
}
variable "search_service_principal_id" {
  type        = string
  description = "Principal ID of the Search service system-assigned managed identity."
  default     = null
}
variable "cosmos_database_name" {
  type        = string
  description = "Cosmos DB SQL database name for conversation persistence."
}
variable "cosmos_container_name" {
  type        = string
  description = "Cosmos DB SQL container name for conversation persistence."
}
variable "cosmos_orchestration_container_name" {
  type        = string
  description = "Cosmos DB SQL container name for orchestration state persistence."
}
variable "tags" {
  type    = map(string)
  default = {}
}
