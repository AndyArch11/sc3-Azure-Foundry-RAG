variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "suffix" { type = string }
variable "scope_ids" { type = map(string) }
variable "deployment_principal_object_id" {
  type        = string
  description = "Object ID of the principal running Terraform; assigned Azure AI Project Manager on Foundry."
}
variable "tags" {
  type    = map(string)
  default = {}
}
