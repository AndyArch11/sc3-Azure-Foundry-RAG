variable "aws_region" {
  type        = string
  description = "AWS region for all resources."
}

variable "aws_region_short" {
  type        = string
  description = "Short region code used in naming (e.g. apse2 for ap-southeast-2)."
}

variable "project" {
  type        = string
  description = "Project name used in resource naming."
  default     = "rag"
}

variable "environment" {
  type        = string
  description = "Environment name (dev/test/prod)."
}

# ── Network ────────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR block."
  default     = "10.30.0.0/16"
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "CIDR blocks for private subnets (one per AZ, minimum 2)."
  default     = ["10.30.1.0/24", "10.30.2.0/24"]
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "CIDR blocks for public subnets (one per AZ, minimum 2; used for NAT gateways)."
  default     = ["10.30.101.0/24", "10.30.102.0/24"]
}

# ── Data services ──────────────────────────────────────────────────────────────

variable "opensearch_engine_version" {
  type        = string
  description = "OpenSearch engine version."
  default     = "OpenSearch_2.13"
}

variable "opensearch_instance_type" {
  type        = string
  description = "OpenSearch data node instance type."
  default     = "r6g.large.search"
}

variable "opensearch_instance_count" {
  type        = number
  description = "Number of OpenSearch data nodes."
  default     = 1
}

variable "opensearch_volume_size_gb" {
  type        = number
  description = "EBS volume size per OpenSearch data node (GB)."
  default     = 20
}

variable "ensure_opensearch_service_linked_role" {
  type        = bool
  description = "Create the OpenSearch service-linked role. Enable only for first-time account bootstrap when the role is absent."
  default     = false
}

variable "dynamodb_table_name" {
  type        = string
  description = "DynamoDB table name for polling state store."
  default     = ""
}

# ── Container images ───────────────────────────────────────────────────────────

variable "query_web_image_tag" {
  type        = string
  description = "Image tag for the query_web container."
  default     = "latest"
}

variable "ingestion_image_tag" {
  type        = string
  description = "Image tag for the ingestion container."
  default     = "latest"
}

# ── ECS / app hosting ─────────────────────────────────────────────────────────

variable "query_web_cpu" {
  type        = number
  description = "CPU units for query_web Fargate task (1 vCPU = 1024)."
  default     = 512
}

variable "query_web_memory_mb" {
  type        = number
  description = "Memory (MiB) for query_web Fargate task."
  default     = 1024
}

variable "query_web_desired_count" {
  type        = number
  description = "Desired number of query_web task replicas."
  default     = 1
}

variable "ingestion_cpu" {
  type        = number
  description = "CPU units for ingestion Fargate task (1 vCPU = 1024)."
  default     = 512
}

variable "ingestion_memory_mb" {
  type        = number
  description = "Memory (MiB) for ingestion Fargate task."
  default     = 1024
}

variable "enable_query_web" {
  type        = bool
  description = "Deploy the query_web ECS service."
  default     = true
}

variable "enable_ingestion_job" {
  type        = bool
  description = "Deploy the ingestion ECS scheduled task."
  default     = false
}

variable "enable_confluence_poller_service" {
  type        = bool
  description = "Deploy the continuous Confluence poller ECS service."
  default     = false
}

variable "enable_adot_sidecar" {
  type        = bool
  description = "Enable the ADOT collector sidecar in query-web tasks for AMP remote-write metrics."
  default     = false
}

variable "query_web_ingress_mode" {
  type        = string
  description = "Ingress mode for query-web: auto (prod=internal, non-prod=none), none, internal, or public."
  default     = "auto"

  validation {
    condition     = contains(["auto", "none", "internal", "public"], var.query_web_ingress_mode)
    error_message = "query_web_ingress_mode must be one of: auto, none, internal, public."
  }
}

variable "query_web_public_ingress_cidrs" {
  type        = list(string)
  description = "CIDR blocks allowed to reach the public query-web ALB when query_web_ingress_mode=public."
  default     = []
}

variable "query_web_tls_certificate_arn" {
  type        = string
  description = "ACM certificate ARN to enable HTTPS on the query-web ALB. Leave empty to keep HTTP-only ingress."
  default     = ""
}

variable "query_web_tls_ssl_policy" {
  type        = string
  description = "SSL policy for the query-web ALB HTTPS listener when query_web_tls_certificate_arn is set."
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
}

variable "confluence_poller_image_tag" {
  type        = string
  description = "Image tag for the Confluence poller container."
  default     = "latest"
}

variable "confluence_poller_cpu" {
  type        = number
  description = "CPU units for confluence poller Fargate task (1 vCPU = 1024)."
  default     = 512
}

variable "confluence_poller_memory_mb" {
  type        = number
  description = "Memory (MiB) for confluence poller Fargate task."
  default     = 1024
}

variable "confluence_base_url" {
  type        = string
  description = "Confluence base URL for polling and content access."
  default     = ""
}

variable "confluence_auth_mode" {
  type        = string
  description = "Confluence auth mode for the poller: basic, bearer, or oauth."
  default     = "basic"
}

variable "confluence_auth_email" {
  type        = string
  description = "Confluence auth email used in basic mode."
  default     = ""
}

variable "confluence_cloud_id" {
  type        = string
  description = "Optional Confluence cloud ID used by bearer or oauth modes."
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
  description = "Polling interval for the Confluence poller ECS service in seconds."
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

variable "initial_confluence_api_token" {
  type        = string
  description = "Initial placeholder value for the Confluence API token secret field. Update out-of-band for existing environments."
  default     = ""
  sensitive   = true
}

# ── LLM / Bedrock ─────────────────────────────────────────────────────────────

variable "bedrock_model_id" {
  type        = string
  description = "Bedrock model ID used for completions."
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "bedrock_embedding_model_id" {
  type        = string
  description = "Bedrock model ID used for embeddings."
  default     = "amazon.titan-embed-text-v2:0"
}

# ── Search ─────────────────────────────────────────────────────────────────────

variable "search_index_name" {
  type        = string
  description = "OpenSearch index name for grounding data."
  default     = "grounding-index"
}

variable "controls_index_name" {
  type        = string
  description = "OpenSearch index name for compliance controls."
  default     = "controls-index"
}
