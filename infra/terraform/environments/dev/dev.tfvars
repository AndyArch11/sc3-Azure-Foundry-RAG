subscription_id              = "d34a087c-ea6c-42d8-a69e-79cb297e48bb"
location                     = "australiaeast"
location_short               = "aue"
environment                  = "dev"
resource_group_name          = "rg-ai-platform-dev"
instance                     = "20260408"
vnet_cidr                    = "10.20.0.0/16"
private_endpoint_subnet_cidr = "10.20.1.0/24"
agent_subnet_cidr            = "10.20.2.0/24"
container_apps_subnet_cidr   = "10.20.5.0/24"
jumpbox_subnet_cidr          = "10.20.3.0/24"
azure_bastion_subnet_cidr    = "10.20.4.0/26"
jumpbox_admin_ssh_public_key = "<set-me-ssh-public-key>"
jumpbox_vm_size              = "Standard_D2s_v3"
enable_model_deployments     = true
enable_ingestion_job         = true
enable_query_web_app         = true
enable_confluence_poller_app = true
query_web_public_endpoint    = true                   # Set true for public query web ingress. Creation-level: switching later requires CAE replacement.
ingestion_job_image_tag      = "202604121152-7c49b27" # Immutable tag. Update when a new ingestion image is pushed.
query_web_image_tag          = "202604121153-7c49b27" # Immutable tag. Update when a new query-web image is pushed.
confluence_poller_image_tag  = "202604111036-3a0d5ff" # Immutable tag. Update when a new confluence poller image is pushed.
search_index_name            = "grounding-index"
controls_index_name          = "controls-index"
# Optional overrides for globally-unique resource names (use when 409 name collisions occur).
search_service_name_override  = "srch-dev-aue-20260408"
foundry_account_name_override = "foundry-dev-aue-20260408"
storage_account_name_override = "stdevaue04or4t4u"
acr_name_override             = "acrdevaue04"
cosmos_account_name_override  = "cosmos-dev-aue-04"
log_analytics_workspace_name_override = "law-dev-aue-04"
monitor_workspace_name_override       = "amw-dev-aue-20260408"
agent_runtime_identity_name_override  = "id-agent-runtime-dev-aue-04"
query_top_k                   = 5
query_default_temperature     = 1.0
query_evaluator_temperature   = 1.0
query_eval_threshold          = 0.72
control_llm_review_enabled    = false
control_llm_review_heuristic_threshold = 0.75

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
confluence_poll_dry_run            = false

# Prompt injection validator settings.
prompt_injection_validator_enabled   = true
prompt_injection_validator_mode      = "shadow"
prompt_injection_validator_threshold = 0.85
prompt_injection_validator_temperature = 0.5
prompt_injection_validator_timeout_s = 15
guardrail_metrics_in_response        = true
# prompt_injection_validator_deployment = "gpt-4.1-mini" # Optional existing deployment override. Leave unset to use validator_model.name.
# validator_model = {
#   name     = "gpt-4.1-mini"
#   version  = "2025-04-14"
#   capacity = 1
# }

# Optional shared token for query web app auth gate.
# query_web_auth_token       = "change-me"
# Optional Entra security group object ID required for query web access.
query_web_required_group_object_id = "7c110a48-68ac-4514-ae8f-1f674091b559"

# Bring-Your-Own-Network (BYOL): uncomment and set these to use pre-existing network infrastructure.
# Leave empty to create network via phase2-network-dns.sh.
# byol_vnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet"
# byol_container_apps_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/container-apps"
# byol_private_endpoint_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/private-endpoints"
# byol_agent_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/agent"
# byol_jumpbox_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/jumpbox"
# byol_azure_bastion_subnet_id = "/subscriptions/..../resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/my-vnet/subnets/AzureBastionSubnet"
