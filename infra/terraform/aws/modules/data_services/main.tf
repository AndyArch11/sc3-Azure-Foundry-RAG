data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ── S3 ─────────────────────────────────────────────────────────────────────────

resource "random_id" "s3_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "grounding_data" {
  # S3 bucket names are globally unique; append a random suffix.
  bucket        = "rag-grounding-${var.naming_suffix}-${random_id.s3_suffix.hex}"
  force_destroy = false

  tags = { Name = "rag-grounding-${var.naming_suffix}" }
}

resource "aws_s3_bucket_versioning" "grounding_data" {
  bucket = aws_s3_bucket.grounding_data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "grounding_data" {
  bucket = aws_s3_bucket.grounding_data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "grounding_data" {
  bucket                  = aws_s3_bucket.grounding_data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── OpenSearch ─────────────────────────────────────────────────────────────────

resource "aws_iam_service_linked_role" "opensearch" {
  count = var.ensure_opensearch_service_linked_role ? 1 : 0

  aws_service_name = "opensearchservice.amazonaws.com"
  description      = "Service-linked role required by Amazon OpenSearch Service for VPC domain management."
}

resource "aws_opensearch_domain" "this" {
  domain_name    = "rag-${var.naming_suffix}"
  engine_version = var.opensearch_engine_version

  depends_on = [aws_iam_service_linked_role.opensearch]

  cluster_config {
    instance_type  = var.opensearch_instance_type
    instance_count = var.opensearch_instance_count
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.opensearch_volume_size_gb
  }

  vpc_options {
    subnet_ids         = slice(var.private_subnet_ids, 0, 1)
    security_group_ids = [var.opensearch_sg_id]
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_security_options {
    enabled = false
  }

  log_publishing_options {
    cloudwatch_log_group_arn = var.opensearch_log_group_arn
    log_type                 = "INDEX_SLOW_LOGS"
  }

  tags = { Name = "rag-${var.naming_suffix}" }
}

# ── DynamoDB ──────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "state_store" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "source"
  range_key    = "doc_key"

  attribute {
    name = "source"
    type = "S"
  }

  attribute {
    name = "doc_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl_epoch"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = { Name = var.dynamodb_table_name }
}
