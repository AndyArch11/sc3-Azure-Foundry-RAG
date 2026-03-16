variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "delegated_agent_subnet_id" { type = string }
variable "storage_account_id" { type = string }
variable "storage_account_name" { type = string }
variable "search_service_id" { type = string }
variable "search_service_name" { type = string }
variable "cosmosdb_account_id" { type = string }
variable "cosmosdb_account_name" { type = string }
variable "embedding_model" {
  type = object({
    name    = string
    version = string
  })
  default = {
    name    = "text-embedding-ada-002"
    version = "2"
  }
}
variable "query_model" {
  type = object({
    name    = string
    version = string
  })
  default = {
    name    = "gpt-5.1-chat"
    version = "2025-11-13"
  }
}
variable "evaluation_model" {
  type = object({
    name    = string
    version = string
  })
  default = {
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }
}
variable "enable_model_deployments" {
  type        = bool
  description = "Whether to create model deployments in the Foundry account. Keep false until model names/quotas are validated for the target region."
  default     = false
}
variable "tags" {
  type    = map(string)
  default = {}
}
