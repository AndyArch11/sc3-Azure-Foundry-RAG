variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "search_service_name_override" {
  type        = string
  description = "Optional explicit Azure AI Search service name override. Leave empty to use srch-<suffix>."
  default     = ""
}
variable "storage_account_name_override" {
  type        = string
  description = "Optional explicit Storage account name override. Leave empty to use generated st<suffix><random>."
  default     = ""
}
variable "acr_name_override" {
  type        = string
  description = "Optional explicit ACR name override. Leave empty to use acr<suffix-without-dashes>."
  default     = ""
}
variable "cosmos_account_name_override" {
  type        = string
  description = "Optional explicit Cosmos DB account name override. Leave empty to use cosmos-<suffix>."
  default     = ""
}
variable "cosmos_database_name" { type = string }
variable "cosmos_container_name" { type = string }
variable "cosmos_orchestration_container_name" {
  type        = string
  description = "Cosmos DB SQL container name for orchestration state (polling watermark/locks)."
  default     = "orchestration-state"
}
variable "tags" {
  type    = map(string)
  default = {}
}
