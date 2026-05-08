variable "naming_suffix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "opensearch_sg_id" {
  type = string
}

variable "opensearch_engine_version" {
  type    = string
  default = "OpenSearch_2.13"
}

variable "opensearch_instance_type" {
  type    = string
  default = "r6g.large.search"
}

variable "opensearch_instance_count" {
  type    = number
  default = 1
}

variable "opensearch_volume_size_gb" {
  type    = number
  default = 20
}

variable "opensearch_log_group_arn" {
  type        = string
  description = "CloudWatch log group ARN for OpenSearch slow logs."
  default     = ""
}

variable "ensure_opensearch_service_linked_role" {
  type        = bool
  description = "Create the OpenSearch service-linked role when missing. Disable only if your IAM policy blocks role discovery/creation and the role already exists."
  default     = true
}

variable "dynamodb_table_name" {
  type = string
}
