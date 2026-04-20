output "ecs_cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "query_web_service_name" {
  value = var.enable_query_web ? aws_ecs_service.query_web[0].name : ""
}

output "ingestion_task_definition_arn" {
  value = var.enable_ingestion_job ? aws_ecs_task_definition.ingestion[0].arn : ""
}
