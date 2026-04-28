output "vpc_id" {
  description = "VPC ID."
  value       = module.network.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs."
  value       = module.network.private_subnet_ids
}

output "s3_bucket_name" {
  description = "S3 grounding-data bucket name."
  value       = module.data_services.s3_bucket_name
}

output "opensearch_endpoint" {
  description = "OpenSearch domain endpoint (HTTPS)."
  value       = module.data_services.opensearch_endpoint
}

output "dynamodb_table_name" {
  description = "DynamoDB state store table name."
  value       = module.data_services.dynamodb_table_name
}

output "query_web_repository_url" {
  description = "ECR repository URL for query_web."
  value       = module.container_registry.query_web_repository_url
}

output "ingestion_repository_url" {
  description = "ECR repository URL for ingestion."
  value       = module.container_registry.ingestion_repository_url
}

output "task_role_arn" {
  description = "IAM task role ARN used by ECS tasks."
  value       = module.identity.task_role_arn
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN."
  value       = module.app_hosting.ecs_cluster_arn
}

output "amp_workspace_id" {
  description = "Amazon Managed Prometheus workspace ID."
  value       = module.observability.amp_workspace_id
}

output "amp_remote_write_url" {
  description = "Amazon Managed Prometheus remote-write URL for ADOT collectors."
  value       = module.observability.amp_remote_write_url
}

output "query_web_service_name" {
  description = "ECS service name for query_web."
  value       = module.app_hosting.query_web_service_name
}
