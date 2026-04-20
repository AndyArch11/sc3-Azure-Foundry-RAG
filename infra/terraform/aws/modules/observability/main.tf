locals {
  log_groups = {
    "query-web"  = "/ecs/${var.naming_suffix}/query-web"
    "ingestion"  = "/ecs/${var.naming_suffix}/ingestion"
    "opensearch" = "/aws/opensearch/${var.naming_suffix}"
  }
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = local.log_groups
  name              = each.value
  retention_in_days = 30

  tags = { Name = each.value }
}
