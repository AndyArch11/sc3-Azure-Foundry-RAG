variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "storage_account_name_prefix" { type = string }
variable "enable_bootstrap_key_vault" {
  type        = bool
  description = "Whether to create the optional bootstrap Key Vault and related RBAC assignments."
  default     = true
}
variable "key_vault_name_prefix" {
  type        = string
  description = "Prefix used to generate globally unique Key Vault names."
  default     = "kvtfstate"
}
variable "key_vault_extra_rbac_principal_object_ids" {
  type        = list(string)
  description = "Optional extra Entra object IDs granted Key Vault Secrets Officer in addition to the current Terraform caller identity."
  default     = []
}
variable "tags" {
  type    = map(string)
  default = {}
}
