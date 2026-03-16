variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "delegated_agent_subnet_id" { type = string }
variable "foundry_project_id" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
