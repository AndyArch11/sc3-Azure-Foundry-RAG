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

output "confluence_poller_repository_url" {
  description = "ECR repository URL for confluence-poller."
  value       = module.container_registry.confluence_poller_repository_url
}

output "task_role_arn" {
  description = "IAM task role ARN used by ECS tasks."
  value       = module.identity.task_role_arn
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN."
  value       = module.app_hosting.ecs_cluster_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = module.app_hosting.ecs_cluster_name
}

output "ingestion_task_definition_arn" {
  description = "ECS task definition ARN for the ingestion container."
  value       = module.app_hosting.ingestion_task_definition_arn
}

output "ecs_sg_id" {
  description = "Security group ID used by ECS tasks."
  value       = module.network.ecs_sg_id
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

output "confluence_poller_service_name" {
  description = "ECS service name for the continuous confluence poller."
  value       = module.app_hosting.confluence_poller_service_name
}

output "query_web_lb_dns_name" {
  description = "DNS name of the ALB fronting query_web, if ingress is enabled."
  value       = module.app_hosting.query_web_lb_dns_name
}

output "query_web_url" {
  description = "Base URL of the ALB fronting query_web, including https when TLS is enabled."
  value       = module.app_hosting.query_web_url
}
