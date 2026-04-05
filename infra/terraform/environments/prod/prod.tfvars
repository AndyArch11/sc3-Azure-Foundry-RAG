subscription_id              = "<set-me>"
location                     = "australiaeast"
location_short               = "aue"
environment                  = "prod"
resource_group_name          = "rg-ai-platform-prod"
vnet_cidr                    = "10.40.0.0/16"
private_endpoint_subnet_cidr = "10.40.1.0/24"
agent_subnet_cidr            = "10.40.2.0/24"
container_apps_subnet_cidr   = "10.40.5.0/24"
jumpbox_subnet_cidr          = "10.40.3.0/24"
azure_bastion_subnet_cidr    = "10.40.4.0/26"
jumpbox_admin_ssh_public_key = "<set-me-ssh-public-key>"
jumpbox_vm_size              = "Standard_B4as_v2"
enable_model_deployments     = false
enable_ingestion_job         = false
enable_query_web_app         = false
enable_confluence_poller_app = false
query_web_public_endpoint    = false    # Set true for public query web ingress. Creation-level: switching later requires CAE replacement.
ingestion_job_image_tag      = "latest" # Set to an immutable tag during deployment.
query_web_image_tag          = "latest" # Set to an immutable tag during deployment.
confluence_poller_image_tag  = "latest" # Set to an immutable tag during deployment.
search_index_name            = "grounding-index"
# Optional overrides for globally-unique resource names (use when 409 name collisions occur).
# search_service_name_override = "srch-prod-aue-20260329"
# foundry_account_name_override = "foundry-prod-aue-20260329"
query_top_k               = 5
query_default_temperature = 1.0
query_evaluator_temperature = 1.0
query_eval_threshold      = 0.72

# Confluence poller settings (keep secrets out of tfvars where possible; pass via secure pipeline vars).
confluence_base_url                = ""
confluence_auth_mode               = "basic"
confluence_auth_email              = ""
confluence_api_token               = ""
confluence_cloud_id                = ""
confluence_account_id              = ""
confluence_poll_space_keys         = []
confluence_poll_interval_seconds   = 75
confluence_poll_lease_ttl_seconds  = 300
confluence_poll_max_event_attempts = 3
confluence_poll_initial_lookback   = "PT1H"
confluence_poll_dry_run            = true

# Optional prompt injection validator settings.
# prompt_injection_validator_enabled    = false
# prompt_injection_validator_mode       = "off" # off | shadow | enforce
# prompt_injection_validator_threshold  = 0.85
# prompt_injection_validator_temperature = 0.5
# prompt_injection_validator_timeout_s  = 15
# prompt_injection_validator_deployment = "gpt-4.1-mini" # Optional existing deployment override. Leave unset to use validator_model.name.
# validator_model = {
#   name     = "gpt-4.1-mini"
#   version  = "2025-04-14"
#   capacity = 1
# }
# guardrail_metrics_in_response = false  # Set true to surface guardrail metrics in API responses. Disable in prod.

# Optional shared token for query web app auth gate.
# query_web_auth_token       = "change-me"
# Optional Entra security group object ID required for query web access.
# query_web_required_group_object_id = "00000000-0000-0000-0000-000000000000"

# Bring-Your-Own-Network (BYOL): uncomment and set these to use pre-existing network infrastructure.
# Leave empty to create network via phase2-network-dns.sh.
# byol_vnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet"
# byol_container_apps_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/container-apps"
# byol_private_endpoint_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/private-endpoints"
# byol_agent_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/agent"
# byol_jumpbox_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/jumpbox"
# byol_azure_bastion_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/AzureBastionSubnet"
