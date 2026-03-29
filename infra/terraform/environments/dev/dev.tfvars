subscription_id              = "<set-me>"
location                     = "australiaeast"
location_short               = "aue"
environment                  = "dev"
resource_group_name          = "rg-ai-platform-dev"
vnet_cidr                    = "10.20.0.0/16"
private_endpoint_subnet_cidr = "10.20.1.0/24"
agent_subnet_cidr            = "10.20.2.0/24"
container_apps_subnet_cidr   = "10.20.5.0/24"
jumpbox_subnet_cidr          = "10.20.3.0/24"
azure_bastion_subnet_cidr    = "10.20.4.0/26"
jumpbox_admin_ssh_public_key = "<set-me-ssh-public-key>"
jumpbox_vm_size              = "Standard_B2as_v2"
enable_model_deployments     = true
enable_ingestion_job         = true
enable_query_web_app         = true
query_web_public_endpoint    = true # Set true for public query web ingress. Creation-level: switching later requires CAE replacement.
ingestion_job_image_tag      = "202603231210-ccf1b68" # Immutable tag. Update when a new ingestion image is pushed.
query_web_image_tag          = "202603231505-1a38698" # Immutable tag. Update when a new query-web image is pushed.
search_index_name            = "grounding-index"
query_top_k                  = 5
query_default_temperature    = 1.0
query_eval_threshold         = 0.72
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
