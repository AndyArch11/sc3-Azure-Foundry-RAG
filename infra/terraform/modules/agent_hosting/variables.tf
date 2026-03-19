variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "delegated_agent_subnet_id" { type = string }
variable "vnet_id" { type = string }
variable "log_analytics_workspace_id" { type = string }
variable "acr_login_server" { type = string }
variable "agent_runtime_identity_id" { type = string }
variable "agent_runtime_client_id" { type = string }
variable "agent_runtime_principal_id" { type = string }
variable "azure_search_endpoint" { type = string }
variable "azure_openai_endpoint" { type = string }
variable "azure_cosmos_endpoint" { type = string }
variable "cosmos_database_name" { type = string }
variable "cosmos_container_name" { type = string }
variable "storage_account_name" { type = string }
variable "storage_account_id" { type = string }
variable "embedding_deployment_name" { type = string }
variable "query_deployment_name" { type = string }
variable "evaluator_deployment_name" { type = string }
variable "search_index_name" { type = string }
variable "embedding_dimensions" { type = number }
variable "query_top_k" { type = number }
variable "query_default_temperature" { type = number }
variable "query_eval_threshold" { type = number }
variable "query_web_auth_token" { type = string }
variable "ingestion_job_image_tag" { type = string }
variable "query_web_image_tag" { type = string }
variable "enable_ingestion_job" {
  type        = bool
  description = "Whether to create the ingestion Container App Job."
  default     = false
}
variable "enable_query_web_app" {
  type        = bool
  description = "Whether to create the internal query web Container App."
  default     = false
}
variable "tags" {
  type    = map(string)
  default = {}
}
