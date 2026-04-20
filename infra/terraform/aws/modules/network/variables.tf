variable "naming_suffix" {
  type        = string
  description = "Naming suffix applied to all resources."
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR block."
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "CIDR blocks for private subnets."
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "CIDR blocks for public subnets."
}
