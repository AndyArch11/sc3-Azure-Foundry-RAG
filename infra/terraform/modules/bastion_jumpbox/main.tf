resource "azurerm_public_ip" "bastion" {
  name                = "pip-bastion-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_bastion_host" "this" {
  name                = "bas-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  ip_configuration {
    name                 = "configuration"
    subnet_id            = var.azure_bastion_subnet_id
    public_ip_address_id = azurerm_public_ip.bastion.id
  }
}

resource "azurerm_network_interface" "jumpbox" {
  name                = "nic-jumpbox-${var.suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = var.jumpbox_subnet_id
    private_ip_address_allocation = "Dynamic"
  }

  tags = var.tags
}

resource "azurerm_linux_virtual_machine" "jumpbox" {
  name                = "vm-jumpbox-${var.suffix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  size                = var.jumpbox_vm_size
  admin_username      = "azureuser"
  network_interface_ids = [
    azurerm_network_interface.jumpbox.id
  ]
  disable_password_authentication = true

  admin_ssh_key {
    username   = "azureuser"
    public_key = var.jumpbox_admin_ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts"
    version   = "latest"
  }

  tags = var.tags
}
