variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "foundry_account_name_override" {
  type        = string
  description = "Optional explicit Foundry/Cognitive account name override. Leave empty to use foundry-<suffix>."
  default     = ""
}
variable "delegated_agent_subnet_id" { type = string }
variable "storage_account_id" { type = string }
variable "storage_account_name" { type = string }
variable "search_service_id" { type = string }
variable "search_service_name" { type = string }
variable "cosmosdb_account_id" { type = string }
variable "cosmosdb_account_name" { type = string }
variable "embedding_model" {
  type = object({
    name     = string
    version  = string
    capacity = optional(number, 10)
  })
  default = {
    name     = "text-embedding-ada-002"
    version  = "2"
    capacity = 10
  }
}
variable "query_model" {
  type = object({
    name     = string
    version  = string
    capacity = optional(number, 1)
  })
  default = {
    name     = "gpt-5.1-chat"
    version  = "2025-11-13"
    capacity = 1
  }
}
variable "evaluation_model" {
  type = object({
    name     = string
    version  = string
    capacity = optional(number, 1)
  })
  default = {
    name     = "gpt-4.1-mini"
    version  = "2025-04-14"
    capacity = 1
  }
}
variable "validator_model" {
  type = object({
    name     = string
    version  = string
    capacity = optional(number, 1)
  })
  default = {
    name     = "gpt-4.1-mini"
    version  = "2025-04-14"
    capacity = 1
  }
}
variable "enable_model_deployments" {
  type        = bool
  description = "Whether to create model deployments in the Foundry account. Keep false until model names/quotas are validated for the target region."
  default     = false
}
variable "foundry_network_acl_bypass_azure_services" {
  type        = bool
  description = "Whether to allow AzureServices bypass on Foundry network ACLs. Keep false for strict private-network posture; set true only as a compatibility fallback."
  default     = false
}
variable "tags" {
  type    = map(string)
  default = {}
}
