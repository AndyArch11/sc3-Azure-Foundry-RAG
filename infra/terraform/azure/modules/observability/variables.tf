variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "workspace_name" { type = string }
variable "monitor_workspace_name_override" {
  type        = string
  description = "Optional explicit Azure Monitor workspace name override. Leave empty to derive from Log Analytics workspace name."
  default     = ""
}
variable "tags" {
  type    = map(string)
  default = {}
}
