variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "vnet_name" { type = string }
variable "vnet_cidr" { type = string }
variable "private_endpoint_subnet_cidr" { type = string }
variable "agent_subnet_cidr" { type = string }
variable "jumpbox_subnet_cidr" { type = string }
variable "azure_bastion_subnet_cidr" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
