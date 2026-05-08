locals {
  naming_suffix = "${var.project}-${var.environment}-${var.aws_region_short}"
  query_web_effective_ingress_mode = (
    var.query_web_ingress_mode == "auto"
    ? (var.environment == "prod" ? "internal" : "none")
    : var.query_web_ingress_mode
  )

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
