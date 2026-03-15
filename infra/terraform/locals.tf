locals {
  naming_suffix = "${var.environment}-${var.location_short}-${var.instance}"

  tags = merge(
    {
      environment = var.environment
      workload    = "ai-cyber-safety"
      managed_by  = "terraform"
    },
    var.tags
  )
}
