variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "private_endpoint_subnet_id" { type = string }
variable "private_dns_zone_ids" { type = map(string) }
variable "storage_account_id" { type = string }
variable "search_service_id" { type = string }
variable "cosmosdb_account_id" { type = string }
variable "foundry_account_id" { type = string }
variable "acr_id" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}
