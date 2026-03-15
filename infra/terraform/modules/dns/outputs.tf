output "private_dns_zone_ids" {
  value = { for name, zone in azurerm_private_dns_zone.zones : name => zone.id }
}

output "conditional_forwarder_target_ip" {
  value = "168.63.129.16"
}
