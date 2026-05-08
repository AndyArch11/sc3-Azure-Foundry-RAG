output "query_web_log_group_name" {
  value = aws_cloudwatch_log_group.this["query-web"].name
}

output "ingestion_log_group_name" {
  value = aws_cloudwatch_log_group.this["ingestion"].name
}

output "confluence_poller_log_group_name" {
  value = aws_cloudwatch_log_group.this["confluence-poller"].name
}

output "opensearch_log_group_name" {
  value = aws_cloudwatch_log_group.this["opensearch"].name
}

output "opensearch_log_group_arn" {
  value = aws_cloudwatch_log_group.this["opensearch"].arn
}

output "log_group_arns" {
  value = [for g in aws_cloudwatch_log_group.this : g.arn]
}

output "amp_workspace_id" {
  value = aws_prometheus_workspace.this.id
}

output "amp_workspace_arn" {
  value = aws_prometheus_workspace.this.arn
}

output "amp_workspace_alias" {
  value = aws_prometheus_workspace.this.alias
}

output "amp_remote_write_url" {
  value = "https://aps-workspaces.${data.aws_region.current.name}.amazonaws.com/workspaces/${aws_prometheus_workspace.this.id}/api/v1/remote_write"
}
