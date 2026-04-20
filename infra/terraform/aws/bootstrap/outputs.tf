output "state_bucket_name" {
  description = "Name of the S3 bucket for Terraform remote state."
  value       = aws_s3_bucket.state.id
}

output "state_bucket_arn" {
  description = "ARN of the S3 state bucket."
  value       = aws_s3_bucket.state.arn
}

output "lock_table_name" {
  description = "Name of the DynamoDB table for Terraform state locking."
  value       = aws_dynamodb_table.lock.name
}

output "bootstrap_secret_arn" {
  description = "ARN of the optional bootstrap Secrets Manager secret. Null when enable_bootstrap_secrets_manager is false."
  value       = try(aws_secretsmanager_secret.bootstrap[0].arn, null)
}
