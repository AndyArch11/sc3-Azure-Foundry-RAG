variable "subscription_id" {
  type        = string
  description = "Azure subscription ID."
}

variable "location" {
  type        = string
  description = "Azure region for all resources."
}

variable "location_short" {
  type        = string
  description = "Short location code used in naming."
}

variable "environment" {
  type        = string
  description = "Environment name (dev/test/prod)."
}

variable "instance" {
  type        = string
  description = "Instance discriminator for naming uniqueness."
  default     = "001"
}

variable "resource_group_name" {
  type        = string
  description = "Primary resource group name."
}

variable "vnet_cidr" {
  type        = string
  description = "VNet CIDR block (/16)."
}

variable "private_endpoint_subnet_cidr" {
  type        = string
  description = "Private endpoint subnet CIDR block (/24)."
}

variable "agent_subnet_cidr" {
  type        = string
  description = "Delegated agent subnet CIDR block (/24)."
}

variable "container_apps_subnet_cidr" {
  type        = string
  description = "Dedicated Container Apps managed environment subnet CIDR block (/24)."
  default     = "10.20.5.0/24"
}

variable "jumpbox_subnet_cidr" {
  type        = string
  description = "Jumpbox subnet CIDR block."
}

variable "azure_bastion_subnet_cidr" {
  type        = string
  description = "Azure Bastion subnet CIDR block."
}

variable "jumpbox_admin_ssh_public_key" {
  type        = string
  description = "SSH public key for jumpbox admin access."
}

variable "bootstrap_key_vault_name" {
  type        = string
  description = "Optional bootstrap Key Vault name used to read runtime convenience secrets such as the jumpbox SSH public key."
  default     = ""
}

variable "bootstrap_key_vault_resource_group_name" {
  type        = string
  description = "Optional bootstrap Key Vault resource group name. Defaults to rg-tfstate-<environment> when unset."
  default     = ""
}

variable "jumpbox_ssh_public_key_secret_name" {
  type        = string
  description = "Optional Key Vault secret name for jumpbox SSH public key. Defaults to jumpbox-admin-ssh-public-key-<environment> when unset."
  default     = ""
}

variable "jumpbox_vm_size" {
  type        = string
  description = "VM size for the jumpbox host."
}

variable "embedding_model" {
  type = object({
    name    = string
    version = string
    capacity = optional(number, 10)
  })
  description = "Embedding model deployment name and version."
  default = {
    name    = "text-embedding-ada-002"
    version = "2"
    capacity = 10
  }
}

variable "query_model" {
  type = object({
    name    = string
    version = string
    capacity = optional(number, 1)
  })
  description = "Query model deployment name and version."
  default = {
    name    = "gpt-5.1-chat"
    version = "2025-11-13"
    capacity = 1
  }
}

variable "evaluation_model" {
  type = object({
    name    = string
    version = string
    capacity = optional(number, 1)
  })
  description = "Evaluation model deployment name and version."
  default = {
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
    capacity = 1
  }
}

variable "enable_model_deployments" {
  type        = bool
  description = "Whether to create Foundry model deployments via Terraform."
  default     = false
}

variable "enable_hosted_query_agent_preview" {
  type        = bool
  description = "Opt-in switch for hosted query agent preview resource. Leave false for standard private-network agent setup."
  default     = false
}

variable "enable_ingestion_job" {
  type        = bool
  description = "Whether to create the Container App Job for ingestion. Keep false until the ingestion image exists in ACR."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Additional tags."
  default     = {}
}
