variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "storage_account_name_prefix" { type = string }
variable "tags" {
	type    = map(string)
	default = {}
}
