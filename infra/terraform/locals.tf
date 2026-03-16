locals {
  naming_suffix = "${var.environment}-${var.location_short}-${var.instance}"

  tags = merge(
    {
      environment = var.environment
      workload    = "ai-platform"
      managed_by  = "terraform"
    },
    var.tags
  )
}
