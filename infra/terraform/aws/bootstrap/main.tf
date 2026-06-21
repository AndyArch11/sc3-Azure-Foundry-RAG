locals {
  naming_suffix = "${var.project}-${var.environment}"
  lock_table_name = coalesce(var.lock_table_name, "tfstate-lock-${local.naming_suffix}")
  default_tags = merge(var.tags, {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform-bootstrap"
  })
}

# ── Remote State S3 Bucket ────────────────────────────────────────────────────
# Stores Terraform state files for the main environment stacks.
# Versioning and deletion protection guard against accidental state loss.

resource "aws_s3_bucket" "state" {
  bucket = "tfstate-${local.naming_suffix}-${random_id.suffix.hex}"
  tags   = local.default_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Force HTTPS-only access to the state bucket.
resource "aws_s3_bucket_policy" "state_https_only" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyNonTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.state.arn,
        "${aws_s3_bucket.state.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}

# ── DynamoDB State Lock Table ─────────────────────────────────────────────────
# Required for Terraform S3 backend state locking.
# Hash key must be "LockID" — this is mandated by the Terraform S3 backend.

resource "aws_dynamodb_table" "lock" {
  name         = local.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.default_tags

  lifecycle {
    prevent_destroy = true
  }
}

# ── Optional Bootstrap Secrets Manager Secret ─────────────────────────────────
# Convenience placeholder for bootstrap-time values (e.g. initial auth token).
# In production, replace or rotate this secret through your organisation's
# secret lifecycle management controls.

resource "aws_secretsmanager_secret" "bootstrap" {
  count                   = var.enable_bootstrap_secrets_manager ? 1 : 0
  name                    = "bootstrap/${local.naming_suffix}"
  description             = "Bootstrap-time values for the ${var.project} ${var.environment} environment."
  recovery_window_in_days = 7
  tags                    = local.default_tags
}

resource "aws_secretsmanager_secret_version" "bootstrap" {
  count     = var.enable_bootstrap_secrets_manager ? 1 : 0
  secret_id = aws_secretsmanager_secret.bootstrap[0].id
  secret_string = jsonencode({
    auth_token = "<set-me>"
  })

  lifecycle {
    # Prevent Terraform from overwriting a secret that has been set externally.
    ignore_changes = [secret_string]
  }
}
