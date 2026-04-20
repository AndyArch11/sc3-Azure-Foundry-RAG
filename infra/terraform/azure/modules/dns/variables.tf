variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "vnet_id" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
