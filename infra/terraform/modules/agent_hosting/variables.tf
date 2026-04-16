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
variable "cosmos_orchestration_container_name" {
  type        = string
  description = "Cosmos DB SQL container name used by orchestration polling state."
}
variable "storage_account_name" { type = string }
variable "storage_account_id" { type = string }
variable "embedding_deployment_name" { type = string }
variable "query_deployment_name" { type = string }
variable "evaluator_deployment_name" { type = string }
variable "search_index_name" { type = string }
variable "controls_index_name" { type = string }
variable "embedding_dimensions" { type = number }
variable "query_top_k" { type = number }
variable "query_default_temperature" { type = number }
variable "query_evaluator_temperature" { type = number }
variable "query_eval_threshold" { type = number }
variable "control_llm_review_enabled" {
  type        = bool
  description = "Enable optional LLM-based control applicability review in the assessment runtime."
  default     = false
}
variable "control_llm_review_heuristic_threshold" {
  type        = number
  description = "Confidence threshold below which controls are sent to the LLM applicability reviewer."
  default     = 0.75
}
variable "prompt_injection_validator_enabled" {
  type        = bool
  description = "Enable optional LLM-based prompt injection validator stage for query web app."
  default     = false
}
variable "prompt_injection_validator_deployment" {
  type        = string
  description = "Model deployment name used for prompt injection validator classification, typically resolved from validator_model.name by the root module."
  default     = "gpt-4.1-mini"
}
variable "prompt_injection_validator_threshold" {
  type        = number
  description = "Confidence threshold for blocking when validator mode is enforce."
  default     = 0.85
}
variable "prompt_injection_validator_temperature" {
  type        = number
  description = "Temperature used for prompt injection validator model calls."
  default     = 0.5
}
variable "prompt_injection_validator_timeout_s" {
  type        = number
  description = "Timeout in seconds for prompt injection validator calls."
  default     = 15
}
variable "prompt_injection_validator_mode" {
  type        = string
  description = "Prompt injection validator mode: off, shadow, or enforce."
  default     = "off"
}
variable "guardrail_metrics_in_response" {
  type        = bool
  description = "Expose guardrail metrics in query web API responses. Enable in dev/test only."
  default     = false
}
variable "query_web_auth_token" { type = string }
variable "query_web_required_group_object_id" {
  type        = string
  description = "Optional Entra ID security group object ID required by query web app."
  default     = ""
}
variable "ingestion_job_image_tag" { type = string }
variable "query_web_image_tag" { type = string }
variable "confluence_poller_image_tag" {
  type        = string
  description = "Image tag for confluence-poller in ACR."
  default     = "latest"
}
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
variable "enable_confluence_poller_app" {
  type        = bool
  description = "Whether to create the dedicated Confluence polling Container App."
  default     = false
}
variable "confluence_base_url" {
  type        = string
  description = "Confluence base URL for polling and content access."
  default     = ""
}
variable "confluence_auth_mode" {
  type        = string
  description = "Confluence auth mode: basic, bearer, or oauth."
  default     = "basic"
}
variable "confluence_auth_email" {
  type        = string
  description = "Confluence auth email used in basic mode."
  default     = ""
}
variable "confluence_api_token" {
  type        = string
  description = "Confluence API token used by poller for basic/bearer mode."
  default     = ""
  sensitive   = true
}
variable "confluence_cloud_id" {
  type        = string
  description = "Optional Confluence cloud ID for bearer/oauth mode."
  default     = ""
}
variable "confluence_account_id" {
  type        = string
  description = "Atlassian account ID used for structured mention CQL polling."
  default     = ""
}
variable "confluence_mention_aliases" {
  type        = list(string)
  description = "Fallback mention aliases used when confluence_account_id is unset (e.g. @compliance-agent)."
  default     = ["@assessment-agent", "@compliance-agent"]
}
variable "confluence_poll_space_keys" {
  type        = list(string)
  description = "Optional list of Confluence space keys included in polling scope."
  default     = []
}
variable "confluence_poll_interval_seconds" {
  type        = number
  description = "Poll interval in seconds."
  default     = 75
}
variable "confluence_poll_lease_ttl_seconds" {
  type        = number
  description = "Lease TTL in seconds for single-flight lock."
  default     = 300
}
variable "confluence_poll_max_event_attempts" {
  type        = number
  description = "Max attempts per event before terminal skip."
  default     = 3
}
variable "confluence_poll_initial_lookback" {
  type        = string
  description = "Initial lookback duration used when no watermark exists."
  default     = "PT1H"
}
variable "confluence_poll_dry_run" {
  type        = bool
  description = "When true, poller runs assess-only and does not post response comments."
  default     = true
}
variable "query_web_public_endpoint" {
  type        = bool
  description = <<-EOT
    Expose the query web app on a public (internet-facing) endpoint instead of the
    internal VNet load balancer. When true, the Container App Environment is created
    with internal_load_balancer_enabled = false — this is a CREATION-LEVEL flag.
    Changing it after the environment exists requires destroying and re-creating the
    environment and all hosted apps.
  EOT
  default     = false
}
variable "query_web_entra_client_id" {
  type        = string
  description = "Entra ID app registration client ID for Container App EasyAuth. Required when query_web_required_group_object_id is non-empty."
  default     = ""
}

variable "query_web_entra_client_secret_key_vault_secret_id" {
  type        = string
  description = "Key Vault secret ID containing Entra ID app registration client secret for Container App EasyAuth."
  default     = ""
}

variable "subscription_id" {
  type        = string
  description = "Subscription ID used by query web to trigger the ingestion Container App Job."
  default     = ""
}

variable "entra_tenant_id" {
  type        = string
  description = "Entra (Azure AD) tenant ID, used to build the OpenID Connect issuer URL for EasyAuth."
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
