variable "naming_suffix" {
  type = string
}

variable "s3_bucket_arn" {
  type = string
}

variable "opensearch_domain_arn" {
  type = string
}

variable "dynamodb_table_arn" {
  type = string
}

variable "ecr_repository_arns" {
  type    = list(string)
  default = []
}

variable "bedrock_model_id" {
  type = string
}

variable "bedrock_embedding_model" {
  type = string
}

variable "log_group_arns" {
  type    = list(string)
  default = []
}

variable "amp_workspace_arn" {
  type        = string
  description = "Amazon Managed Prometheus workspace ARN. Empty disables remote-write permissions."
  default     = ""
}

variable "app_secret_arn" {
  type        = string
  description = "ARN of the Secrets Manager secret for application runtime values. Leave empty to skip policy creation."
  default     = ""
}
