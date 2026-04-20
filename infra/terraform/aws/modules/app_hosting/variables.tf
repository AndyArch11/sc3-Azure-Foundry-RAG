variable "naming_suffix" { type = string }

variable "vpc_id" {
	type = string
}

variable "private_subnet_ids" {
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

variable "query_web_image_tag" {
	type = string
}

variable "ingestion_image_tag" {
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

variable "log_group_name_query_web" {
	type = string
}

variable "log_group_name_ingestion" {
	type = string
}

variable "aws_region" {
	type = string
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
