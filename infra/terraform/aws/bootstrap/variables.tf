variable "aws_region" {
  type        = string
  description = "AWS region for bootstrap resources."
}

variable "project" {
  type        = string
  description = "Project name used in resource naming."
  default     = "rag"
}

variable "environment" {
  type        = string
  description = "Environment name (dev/test/prod). Used in bucket and table names."
}

variable "lock_table_name" {
  type        = string
  description = "Optional DynamoDB lock table name override. When null, defaults to tfstate-lock-<project>-<environment>."
  default     = null
}

variable "enable_bootstrap_secrets_manager" {
  type        = bool
  description = "Whether to create the optional Secrets Manager secret for bootstrap-time values (e.g. initial auth token placeholder)."
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
