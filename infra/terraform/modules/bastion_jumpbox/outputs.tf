output "bastion_host_id" {
  value = azurerm_bastion_host.this.id
}

output "jumpbox_vm_id" {
  value = azurerm_linux_virtual_machine.jumpbox.id
}
