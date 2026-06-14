variable "naming_suffix" {
  type        = string
  description = "Naming suffix applied to all resources."
}

variable "initial_auth_token" {
  type        = string
  description = "Initial placeholder value for the auth_token secret field. Update out-of-band before enabling services."
  default     = "<set-me>"
  sensitive   = true
}

variable "initial_confluence_api_token" {
  type        = string
  description = "Initial placeholder value for the confluence_api_token secret field. Update out-of-band before enabling the poller."
  default     = ""
  sensitive   = true
}

variable "initial_bedrock_mantle_api_key" {
  type        = string
  description = "Initial placeholder value for the bedrock_api_key secret field used by Bedrock Mantle mode."
  default     = ""
  sensitive   = true
}

variable "recovery_window_days" {
  type        = number
  description = "Number of days Secrets Manager waits before deleting a secret (7–30). Set to 0 to disable recovery window."
  default     = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
