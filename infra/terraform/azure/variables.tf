variable "subscription_id" {
  type        = string
  description = "Azure subscription ID."
}

variable "location" {
  type        = string
  description = "Azure region for all resources."
}

variable "location_short" {
  type        = string
  description = "Short location code used in naming."
}

variable "environment" {
  type        = string
  description = "Environment name (dev/test/prod)."
}

variable "instance" {
  type        = string
  description = "Instance discriminator for naming uniqueness."
  default     = "001"
}

variable "resource_group_name" {
  type        = string
  description = "Primary resource group name."
}

variable "vnet_cidr" {
  type        = string
  description = "VNet CIDR block (/16)."
}

variable "private_endpoint_subnet_cidr" {
  type        = string
  description = "Private endpoint subnet CIDR block (/24)."
}

variable "agent_subnet_cidr" {
  type        = string
  description = "Delegated agent subnet CIDR block (/24)."
}

variable "container_apps_subnet_cidr" {
  type        = string
  description = "Dedicated Container Apps managed environment subnet CIDR block (/24)."
  default     = "10.20.5.0/24"
}

variable "jumpbox_subnet_cidr" {
  type        = string
  description = "Jumpbox subnet CIDR block."
}

variable "azure_bastion_subnet_cidr" {
  type        = string
  description = "Azure Bastion subnet CIDR block."
}

variable "jumpbox_admin_ssh_public_key" {
  type        = string
  description = "SSH public key for jumpbox admin access."
}

variable "bootstrap_key_vault_name" {
  type        = string
  description = "Optional bootstrap Key Vault name used to read runtime convenience secrets such as the jumpbox SSH public key."
  default     = ""
}

variable "bootstrap_key_vault_resource_group_name" {
  type        = string
  description = "Optional bootstrap Key Vault resource group name. Defaults to rg-tfstate-<environment> when unset."
  default     = ""
}

variable "jumpbox_ssh_public_key_secret_name" {
  type        = string
  description = "Optional Key Vault secret name for jumpbox SSH public key. Defaults to jumpbox-admin-ssh-public-key-<environment> when unset."
  default     = ""
}

variable "jumpbox_vm_size" {
  type        = string
  description = "VM size for the jumpbox host."
}

variable "embedding_model" {
  type = object({
    name     = string
    version  = string
    capacity = optional(number, 10)
  })
  description = "Embedding model deployment name and version."
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
  description = "Query model deployment name and version."
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
  description = "Evaluation model deployment name and version."
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
  description = "Prompt injection validator model deployment name and version."
  default = {
    name     = "gpt-4.1-mini"
    version  = "2025-04-14"
    capacity = 1
  }
}

variable "enable_model_deployments" {
  type        = bool
  description = "Whether to create Foundry model deployments via Terraform."
  default     = false
}

variable "foundry_network_acl_bypass_azure_services" {
  type        = bool
  description = "Whether to allow AzureServices bypass on Foundry network ACLs. Keep false for strict private-network posture; set true only as a compatibility fallback."
  default     = false
}

variable "enable_hosted_query_agent_preview" {
  type        = bool
  description = "Opt-in switch for hosted query agent preview resource. Leave false for standard private-network agent setup."
  default     = false
}

variable "enable_ingestion_job" {
  type        = bool
  description = "Whether to create the Container App Job for ingestion. Keep false until the ingestion image exists in ACR."
  default     = false
}

variable "enable_query_web_app" {
  type        = bool
  description = "Whether to create the internal query web Container App."
  default     = false
}

variable "query_web_public_endpoint" {
  type        = bool
  description = <<-EOT
    Expose the query web app on a public (internet-facing) endpoint.
    When true, the Container App Environment uses an external load balancer and
    the private DNS zone is omitted. This is a CREATION-LEVEL flag — changing it
    after deployment requires destroying and re-creating the Container App Environment.
  EOT
  default     = false
}

variable "ingestion_job_image_tag" {
  type        = string
  description = "Image tag for ingestion-runner in ACR."
  default     = "latest"
}

variable "query_web_image_tag" {
  type        = string
  description = "Image tag for query-web in ACR."
  default     = "latest"
}

variable "search_index_name" {
  type        = string
  description = "Azure AI Search index name used by query workloads."
  default     = "grounding-index"
}

variable "controls_index_name" {
  type        = string
  description = "Azure AI Search index name used by assessment control retrieval workloads."
  default     = "controls-index"
}

variable "search_service_name_override" {
  type        = string
  description = "Optional explicit Azure AI Search service name override. Leave empty to use srch-<environment>-<location_short>-<instance>."
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

variable "foundry_account_name_override" {
  type        = string
  description = "Optional explicit Foundry/Cognitive account name override. Leave empty to use foundry-<environment>-<location_short>-<instance>."
  default     = ""
}

variable "cosmos_account_name_override" {
  type        = string
  description = "Optional explicit Cosmos DB account name override. Leave empty to use cosmos-<environment>-<location_short>-<instance>."
  default     = ""
}

variable "log_analytics_workspace_name_override" {
  type        = string
  description = "Optional explicit Log Analytics workspace name override. Leave empty to use law-<environment>-<location_short>-<instance>."
  default     = ""
}

variable "monitor_workspace_name_override" {
  type        = string
  description = "Optional explicit Azure Monitor workspace name override. Leave empty to use amw-<law-suffix>."
  default     = ""
}

variable "agent_runtime_identity_name_override" {
  type        = string
  description = "Optional explicit runtime UAMI name override. Leave empty to use id-agent-runtime-<environment>-<location_short>-<instance>."
  default     = ""
}

variable "cosmos_database_name" {
  type        = string
  description = "Cosmos DB SQL database name used by query web conversation persistence."
  default     = "rag-conversations"
}

variable "cosmos_container_name" {
  type        = string
  description = "Cosmos DB SQL container name used by query web conversation persistence."
  default     = "conversations"
}

variable "cosmos_orchestration_container_name" {
  type        = string
  description = "Cosmos DB SQL container name used by orchestration polling state."
  default     = "orchestration-state"
}

variable "enable_confluence_poller_app" {
  type        = bool
  description = "Whether to create the dedicated Confluence polling Container App."
  default     = false
}

variable "confluence_poller_image_tag" {
  type        = string
  description = "Image tag for confluence-poller in ACR."
  default     = "latest"
}

variable "confluence_base_url" {
  type        = string
  description = "Confluence base URL for polling and content access."
  default     = ""
}

variable "confluence_auth_mode" {
  type        = string
  description = "Confluence auth mode for poller: basic, bearer, or oauth."
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
  description = "Optional Confluence cloud ID used by bearer/oauth modes."
  default     = ""
}

variable "confluence_account_id" {
  type        = string
  description = "Atlassian account ID used for structured mention CQL polling."
  default     = ""
}

variable "confluence_mention_aliases" {
  type        = list(string)
  description = "Fallback mention aliases used when confluence_account_id is unset."
  default     = ["@assessment-agent", "@compliance-agent"]
}

variable "confluence_poll_space_keys" {
  type        = list(string)
  description = "Optional allowlist of Confluence space keys for polling scope."
  default     = []
}

variable "confluence_poll_interval_seconds" {
  type        = number
  description = "Polling interval for Confluence poller Container App in seconds."
  default     = 75
}

variable "confluence_poll_lease_ttl_seconds" {
  type        = number
  description = "Distributed lease TTL for poller single-flight control in seconds."
  default     = 300
}

variable "confluence_poll_max_event_attempts" {
  type        = number
  description = "Maximum attempts per event before terminal skip."
  default     = 3
}

variable "confluence_poll_initial_lookback" {
  type        = string
  description = "Initial lookback duration for first poll when no watermark exists."
  default     = "PT1H"
}

variable "confluence_poll_dry_run" {
  type        = bool
  description = "When true, poller detects and assesses events without posting response comments."
  default     = true
}

variable "query_top_k" {
  type        = number
  description = "Number of chunks to retrieve for hybrid search in query web app."
  default     = 5
}

variable "query_default_temperature" {
  type        = number
  description = "Default generation temperature for query web app. Modify subject to model capabilities and tuning. Some models may ignore this value or not work with certain temperature settings."
  default     = 1.0
}

variable "query_evaluator_temperature" {
  type        = number
  description = "Evaluator model temperature for quality checks in query web app. Keep at 1.0 for widest model compatibility unless validated otherwise."
  default     = 1.0
}

variable "query_eval_threshold" {
  type        = number
  description = "Minimum acceptable evaluator score before triggering a second answer attempt."
  default     = 0.72
}

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
  description = "Optional existing deployment name used for prompt injection validator classification. Leave empty to use validator_model.name."
  default     = ""
}

variable "prompt_injection_validator_threshold" {
  type        = number
  description = "Confidence threshold for blocking when validator mode is enforce."
  default     = 0.85
}

variable "prompt_injection_validator_temperature" {
  type        = number
  description = "Prompt injection validator model temperature. Lower values can improve consistency but may not be supported by all models."
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
  description = "Expose guardrail metrics (deterministic score, validator confidence) in query web API responses. Enable in dev/test only; disable in prod."
  default     = false
}

variable "query_web_auth_token" {
  type        = string
  description = "Optional shared token to gate query web app access. Leave empty to disable app-level auth."
  default     = ""
  sensitive   = true
}

variable "query_web_required_group_object_id" {
  type        = string
  description = "Optional Entra ID security group object ID required to access query web app. Leave empty to disable group-based app auth."
  default     = ""
}

variable "query_web_entra_client_secret_key_vault_secret_id" {
  type        = string
  description = "Key Vault secret ID containing the Entra app client secret used by Container App EasyAuth. Populated during jumpbox rollout."
  default     = ""
}


# Bring-Your-Own-Network (BYOL): optional pre-created network resource IDs.
# If provided, phase 2 (network creation) can be skipped entirely.
# Leave empty (default) to create network resources via phase 2.
variable "byol_vnet_id" {
  type        = string
  description = "Optional: resource ID of pre-existing VNet. If provided, network/DNS creation is skipped."
  default     = ""
}
variable "byol_container_apps_subnet_id" {
  type        = string
  description = "Optional: resource ID of pre-existing Container Apps subnet (delegated to Microsoft.App/environments)."
  default     = ""
}
variable "byol_private_endpoint_subnet_id" {
  type        = string
  description = "Optional: resource ID of pre-existing private endpoint subnet."
  default     = ""
}
variable "byol_agent_subnet_id" {
  type        = string
  description = "Optional: resource ID of pre-existing agent subnet (delegated to Microsoft.CognitiveServices)."
  default     = ""
}
variable "byol_jumpbox_subnet_id" {
  type        = string
  description = "Optional: resource ID of pre-existing jumpbox subnet."
  default     = ""
}
variable "byol_azure_bastion_subnet_id" {
  type        = string
  description = "Optional: resource ID of pre-existing Azure Bastion subnet."
  default     = ""
}

variable "tags" {
  type        = map(string)
  description = "Additional tags."
  default     = {}
}
