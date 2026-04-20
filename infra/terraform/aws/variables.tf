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
