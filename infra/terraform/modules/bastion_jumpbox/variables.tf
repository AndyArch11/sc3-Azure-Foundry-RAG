variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "jumpbox_subnet_id" { type = string }
variable "azure_bastion_subnet_id" { type = string }
variable "suffix" { type = string }
variable "jumpbox_admin_ssh_public_key" { type = string }
variable "jumpbox_vm_size" {
  type = string
}
variable "agent_runtime_identity_id" {
  type        = string
  description = "Resource ID of the user-assigned managed identity to attach to the jumpbox."
}
variable "tags" {
  type    = map(string)
  default = {}
}
