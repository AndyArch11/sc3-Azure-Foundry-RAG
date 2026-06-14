variable "naming_suffix" { type = string }

variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "ecs_sg_id" {
  type = string
}

variable "task_execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "query_web_repository_url" {
  type = string
}

variable "ingestion_repository_url" {
  type = string
}

variable "confluence_poller_repository_url" {
  type = string
}

variable "query_web_image_tag" {
  type = string
}

variable "ingestion_image_tag" {
  type = string
}

variable "confluence_poller_image_tag" {
  type = string
}

variable "query_web_cpu" {
  type    = number
  default = 512
}

variable "query_web_memory_mb" {
  type    = number
  default = 1024
}

variable "query_web_desired_count" {
  type    = number
  default = 1
}

variable "enable_query_web" {
  type    = bool
  default = true
}

variable "enable_ingestion_job" {
  type    = bool
  default = false
}

variable "enable_confluence_poller_service" {
  type    = bool
  default = false
}

variable "enable_adot_sidecar" {
  type        = bool
  default     = false
  description = "Enable the ADOT collector sidecar for query-web tasks."
}

variable "query_web_ingress_mode" {
  type        = string
  default     = "none"
  description = "Ingress mode for query-web: none, internal, or public."
}

variable "query_web_public_ingress_cidrs" {
  type        = list(string)
  default     = []
  description = "CIDR blocks allowed to access the public query-web ALB. Required when query_web_ingress_mode=public."
}

variable "query_web_tls_certificate_arn" {
  type        = string
  default     = ""
  description = "ACM certificate ARN to enable HTTPS on the query-web ALB. Leave empty to keep HTTP-only ingress."
}

variable "query_web_tls_ssl_policy" {
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  description = "SSL policy for the query-web ALB HTTPS listener."
}

variable "enable_cluster_capacity_providers" {
  type        = bool
  default     = false
  description = "Attach FARGATE/FARGATE_SPOT capacity providers to the ECS cluster. Requires AWSServiceRoleForECS to exist."
}

variable "log_group_name_query_web" {
  type = string
}

variable "log_group_name_ingestion" {
  type = string
}

variable "log_group_name_confluence_poller" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "prometheus_remote_write_url" {
  type        = string
  default     = ""
  description = "Amazon Managed Prometheus remote-write URL. Empty disables the ADOT metrics sidecar."
}

variable "opensearch_endpoint" {
  type = string
}

variable "s3_bucket_name" {
  type = string
}

variable "dynamodb_table_name" {
  type = string
}

variable "search_index_name" {
  type = string
}

variable "controls_index_name" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

variable "bedrock_embedding_model_id" {
  type = string
}

variable "bedrock_api_mode" {
  type        = string
  description = "Bedrock API mode for ECS services: runtime or mantle."
  default     = "runtime"
}

variable "app_secrets_secret_arn" {
  type        = string
  description = "ARN of the Secrets Manager secret to inject at task start. Empty string disables injection."
  default     = ""
}

variable "ingestion_cpu" {
  type    = number
  default = 512
}

variable "ingestion_memory_mb" {
  type    = number
  default = 1024
}

variable "confluence_poller_cpu" {
  type    = number
  default = 512
}

variable "confluence_poller_memory_mb" {
  type    = number
  default = 1024
}

variable "confluence_base_url" {
  type        = string
  default     = ""
  description = "Confluence base URL for polling and content access."
}

variable "confluence_auth_mode" {
  type        = string
  default     = "basic"
  description = "Confluence auth mode for the poller: basic, bearer, or oauth."
}

variable "confluence_auth_email" {
  type        = string
  default     = ""
  description = "Confluence auth email used in basic mode."
}

variable "confluence_cloud_id" {
  type        = string
  default     = ""
  description = "Optional Confluence cloud ID used by bearer or oauth modes."
}

variable "confluence_account_id" {
  type        = string
  default     = ""
  description = "Atlassian account ID used for structured mention CQL polling."
}

variable "confluence_mention_aliases" {
  type        = list(string)
  default     = ["@assessment-agent", "@compliance-agent"]
  description = "Fallback mention aliases used when confluence_account_id is unset."
}

variable "confluence_poll_space_keys" {
  type        = list(string)
  default     = []
  description = "Optional allowlist of Confluence space keys for polling scope."
}

variable "confluence_poll_interval_seconds" {
  type        = number
  default     = 75
  description = "Polling interval for the Confluence poller ECS service in seconds."
}

variable "confluence_poll_lease_ttl_seconds" {
  type        = number
  default     = 300
  description = "Distributed lease TTL for poller single-flight control in seconds."
}

variable "confluence_poll_max_event_attempts" {
  type        = number
  default     = 3
  description = "Maximum attempts per event before terminal skip."
}

variable "confluence_poll_initial_lookback" {
  type        = string
  default     = "PT1H"
  description = "Initial lookback duration for first poll when no watermark exists."
}

variable "confluence_poll_dry_run" {
  type        = bool
  default     = true
  description = "When true, poller detects and assesses events without posting response comments."
}
