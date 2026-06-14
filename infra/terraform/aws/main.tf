module "network" {
  source               = "./modules/network"
  naming_suffix        = local.naming_suffix
  vpc_cidr             = var.vpc_cidr
  private_subnet_cidrs = var.private_subnet_cidrs
  public_subnet_cidrs  = var.public_subnet_cidrs
}

module "observability" {
  source        = "./modules/observability"
  naming_suffix = local.naming_suffix
}

module "data_services" {
  source                                = "./modules/data_services"
  naming_suffix                         = local.naming_suffix
  vpc_id                                = module.network.vpc_id
  private_subnet_ids                    = module.network.private_subnet_ids
  opensearch_sg_id                      = module.network.opensearch_sg_id
  opensearch_engine_version             = var.opensearch_engine_version
  opensearch_instance_type              = var.opensearch_instance_type
  opensearch_instance_count             = var.opensearch_instance_count
  opensearch_volume_size_gb             = var.opensearch_volume_size_gb
  opensearch_log_group_arn              = module.observability.opensearch_log_group_arn
  ensure_opensearch_service_linked_role = var.ensure_opensearch_service_linked_role
  dynamodb_table_name                   = var.dynamodb_table_name != "" ? var.dynamodb_table_name : "rag-state-${local.naming_suffix}"

  depends_on = [module.observability]
}

module "app_secrets" {
  source                         = "./modules/app_secrets"
  naming_suffix                  = local.naming_suffix
  initial_confluence_api_token   = var.initial_confluence_api_token
  initial_bedrock_mantle_api_key = var.initial_bedrock_mantle_api_key
  tags                           = local.tags
}

module "identity" {
  source                  = "./modules/identity"
  naming_suffix           = local.naming_suffix
  s3_bucket_arn           = module.data_services.s3_bucket_arn
  opensearch_domain_arn   = module.data_services.opensearch_domain_arn
  dynamodb_table_arn      = module.data_services.dynamodb_table_arn
  ecr_repository_arns     = module.container_registry.repository_arns
  bedrock_model_id        = var.bedrock_model_id
  bedrock_embedding_model = var.bedrock_embedding_model_id
  log_group_arns          = module.observability.log_group_arns
  amp_workspace_arn       = module.observability.amp_workspace_arn
  app_secret_arn          = module.app_secrets.secret_arn
}

module "container_registry" {
  source        = "./modules/container_registry"
  naming_suffix = local.naming_suffix
  environment   = var.environment
}

module "app_hosting" {
  source                             = "./modules/app_hosting"
  environment                        = var.environment
  naming_suffix                      = local.naming_suffix
  vpc_id                             = module.network.vpc_id
  vpc_cidr                           = var.vpc_cidr
  private_subnet_ids                 = module.network.private_subnet_ids
  public_subnet_ids                  = module.network.public_subnet_ids
  ecs_sg_id                          = module.network.ecs_sg_id
  task_execution_role_arn            = module.identity.task_execution_role_arn
  task_role_arn                      = module.identity.task_role_arn
  query_web_repository_url           = module.container_registry.query_web_repository_url
  ingestion_repository_url           = module.container_registry.ingestion_repository_url
  confluence_poller_repository_url   = module.container_registry.confluence_poller_repository_url
  query_web_image_tag                = var.query_web_image_tag
  ingestion_image_tag                = var.ingestion_image_tag
  confluence_poller_image_tag        = var.confluence_poller_image_tag
  query_web_cpu                      = var.query_web_cpu
  query_web_memory_mb                = var.query_web_memory_mb
  query_web_desired_count            = var.query_web_desired_count
  ingestion_cpu                      = var.ingestion_cpu
  ingestion_memory_mb                = var.ingestion_memory_mb
  confluence_poller_cpu              = var.confluence_poller_cpu
  confluence_poller_memory_mb        = var.confluence_poller_memory_mb
  enable_query_web                   = var.enable_query_web
  enable_ingestion_job               = var.enable_ingestion_job
  enable_confluence_poller_service   = var.enable_confluence_poller_service
  enable_adot_sidecar                = var.enable_adot_sidecar
  query_web_ingress_mode             = local.query_web_effective_ingress_mode
  query_web_public_ingress_cidrs     = var.query_web_public_ingress_cidrs
  query_web_tls_certificate_arn      = var.query_web_tls_certificate_arn
  query_web_tls_ssl_policy           = var.query_web_tls_ssl_policy
  confluence_base_url                = var.confluence_base_url
  confluence_auth_mode               = var.confluence_auth_mode
  confluence_auth_email              = var.confluence_auth_email
  confluence_cloud_id                = var.confluence_cloud_id
  confluence_account_id              = var.confluence_account_id
  confluence_mention_aliases         = var.confluence_mention_aliases
  confluence_poll_space_keys         = var.confluence_poll_space_keys
  confluence_poll_interval_seconds   = var.confluence_poll_interval_seconds
  confluence_poll_lease_ttl_seconds  = var.confluence_poll_lease_ttl_seconds
  confluence_poll_max_event_attempts = var.confluence_poll_max_event_attempts
  confluence_poll_initial_lookback   = var.confluence_poll_initial_lookback
  confluence_poll_dry_run            = var.confluence_poll_dry_run
  log_group_name_query_web           = module.observability.query_web_log_group_name
  log_group_name_ingestion           = module.observability.ingestion_log_group_name
  log_group_name_confluence_poller   = module.observability.confluence_poller_log_group_name
  aws_region                         = var.aws_region
  prometheus_remote_write_url        = module.observability.amp_remote_write_url
  opensearch_endpoint                = module.data_services.opensearch_endpoint
  s3_bucket_name                     = module.data_services.s3_bucket_name
  dynamodb_table_name                = module.data_services.dynamodb_table_name
  search_index_name                  = var.search_index_name
  controls_index_name                = var.controls_index_name
  bedrock_model_id                   = var.bedrock_model_id
  bedrock_embedding_model_id         = var.bedrock_embedding_model_id
  bedrock_api_mode                   = var.bedrock_api_mode
  app_secrets_secret_arn             = module.app_secrets.secret_arn
}

resource "aws_opensearch_domain_policy" "main" {
  domain_name = module.data_services.opensearch_domain_name

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = module.identity.task_role_arn }
        Action    = "es:*"
        Resource  = "${module.data_services.opensearch_domain_arn}/*"
      }
    ]
  })
}
