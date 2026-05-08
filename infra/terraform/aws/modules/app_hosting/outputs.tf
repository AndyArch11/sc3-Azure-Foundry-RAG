output "ecs_cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "query_web_service_name" {
  value = var.enable_query_web ? aws_ecs_service.query_web[0].name : ""
}

output "confluence_poller_service_name" {
  value = var.enable_confluence_poller_service ? aws_ecs_service.confluence_poller[0].name : ""
}

output "query_web_lb_dns_name" {
  value = local.query_web_ingress_enabled ? aws_lb.query_web[0].dns_name : ""
}

output "query_web_url" {
  value = local.query_web_ingress_enabled ? format("%s://%s", local.query_web_https_enabled ? "https" : "http", aws_lb.query_web[0].dns_name) : ""
}

output "ingestion_task_definition_arn" {
  value = var.enable_ingestion_job ? aws_ecs_task_definition.ingestion[0].arn : ""
}
