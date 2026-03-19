variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "cosmos_database_name" { type = string }
variable "cosmos_container_name" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
