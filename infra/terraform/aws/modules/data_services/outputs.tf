output "s3_bucket_name" {
  value = aws_s3_bucket.grounding_data.bucket
}

output "s3_bucket_arn" {
  value = aws_s3_bucket.grounding_data.arn
}

output "opensearch_endpoint" {
  value = "https://${aws_opensearch_domain.this.endpoint}"
}

output "opensearch_domain_arn" {
  value = aws_opensearch_domain.this.arn
}

output "opensearch_domain_name" {
  value = aws_opensearch_domain.this.domain_name
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.state_store.name
}

output "dynamodb_table_arn" {
  value = aws_dynamodb_table.state_store.arn
}
