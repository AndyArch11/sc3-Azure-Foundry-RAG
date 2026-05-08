data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  log_groups = {
    "query-web"         = "/ecs/${var.naming_suffix}/query-web"
    "ingestion"         = "/ecs/${var.naming_suffix}/ingestion"
    "confluence-poller" = "/ecs/${var.naming_suffix}/confluence-poller"
    "opensearch"        = "/aws/opensearch/${var.naming_suffix}"
  }
}

resource "aws_prometheus_workspace" "this" {
  alias = "sc3-rag-${var.naming_suffix}"
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = local.log_groups
  name              = each.value
  retention_in_days = 30

  tags = { Name = each.value }
}

resource "aws_cloudwatch_log_resource_policy" "opensearch" {
  policy_name = "opensearch-log-publish-${var.naming_suffix}"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowOpenSearchServiceToPublishLogs"
        Effect = "Allow"
        Principal = {
          Service = "es.amazonaws.com"
        }
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          aws_cloudwatch_log_group.this["opensearch"].arn,
          "${aws_cloudwatch_log_group.this["opensearch"].arn}:*",
        ]
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:es:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:domain/*"
          }
        }
      }
    ]
  })
}
