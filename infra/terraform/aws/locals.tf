locals {
  naming_suffix = "${var.project}-${var.environment}-${var.aws_region_short}"

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
