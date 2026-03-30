variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "private_endpoint_subnet_id" { type = string }
variable "private_dns_zone_id" { type = string }
variable "agent_runtime_principal_id" { type = string }

variable "tags" {
  type    = map(string)
  default = {}
}
