variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "api_management_name" { type = string }
variable "publisher_name" {
  type        = string
  description = "Publisher name shown in API Management."
  default     = "Platform Team"
}
variable "publisher_email" {
  type        = string
  description = "Publisher email shown in API Management."
  default     = "platform@example.com"
}
variable "sku_name" {
  type        = string
  description = "API Management SKU. Keep StandardV2_1 for this stack."
  default     = "StandardV2_1"
}
variable "virtual_network_subnet_id" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}