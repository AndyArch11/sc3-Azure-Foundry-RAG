variable "resource_group_name"        { type = string }
variable "location"                   { type = string }
variable "suffix"                     { type = string }
variable "delegated_agent_subnet_id"  { type = string }
variable "log_analytics_workspace_id" { type = string }
variable "acr_login_server"           { type = string }
variable "agent_runtime_identity_id"   { type = string }
variable "agent_runtime_client_id"    { type = string }
variable "agent_runtime_principal_id" { type = string }
variable "azure_search_endpoint"      { type = string }
variable "azure_openai_endpoint"      { type = string }
variable "storage_account_name"       { type = string }
variable "storage_account_id"         { type = string }
variable "embedding_deployment_name"  { type = string }
variable "embedding_dimensions"       { type = number }
variable "enable_ingestion_job" {
  type        = bool
  description = "Whether to create the ingestion Container App Job."
  default     = false
}
variable "tags" {
  type    = map(string)
  default = {}
}
