output "query_web_repository_url" {
  value = aws_ecr_repository.this["query-web"].repository_url
}

output "ingestion_repository_url" {
  value = aws_ecr_repository.this["ingestion"].repository_url
}

output "repository_arns" {
  value = [for r in aws_ecr_repository.this : r.arn]
}
