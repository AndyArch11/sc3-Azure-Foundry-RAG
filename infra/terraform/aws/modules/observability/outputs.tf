output "query_web_log_group_name" {
  value = aws_cloudwatch_log_group.this["query-web"].name
}

output "ingestion_log_group_name" {
  value = aws_cloudwatch_log_group.this["ingestion"].name
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
