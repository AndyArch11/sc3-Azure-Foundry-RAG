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
  source                    = "./modules/data_services"
  naming_suffix             = local.naming_suffix
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  opensearch_sg_id          = module.network.opensearch_sg_id
  opensearch_engine_version = var.opensearch_engine_version
  opensearch_instance_type  = var.opensearch_instance_type
  opensearch_instance_count = var.opensearch_instance_count
  opensearch_volume_size_gb = var.opensearch_volume_size_gb
  opensearch_log_group_arn  = module.observability.opensearch_log_group_arn
  dynamodb_table_name       = var.dynamodb_table_name != "" ? var.dynamodb_table_name : "rag-state-${local.naming_suffix}"
}

module "app_secrets" {
  source        = "./modules/app_secrets"
  naming_suffix = local.naming_suffix
  tags          = local.tags
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
  source                      = "./modules/app_hosting"
  naming_suffix               = local.naming_suffix
  vpc_id                      = module.network.vpc_id
  private_subnet_ids          = module.network.private_subnet_ids
  ecs_sg_id                   = module.network.ecs_sg_id
  task_execution_role_arn     = module.identity.task_execution_role_arn
  task_role_arn               = module.identity.task_role_arn
  query_web_repository_url    = module.container_registry.query_web_repository_url
  ingestion_repository_url    = module.container_registry.ingestion_repository_url
  query_web_image_tag         = var.query_web_image_tag
  ingestion_image_tag         = var.ingestion_image_tag
  query_web_cpu               = var.query_web_cpu
  query_web_memory_mb         = var.query_web_memory_mb
  query_web_desired_count     = var.query_web_desired_count
  ingestion_cpu               = var.ingestion_cpu
  ingestion_memory_mb         = var.ingestion_memory_mb
  enable_query_web            = var.enable_query_web
  enable_ingestion_job        = var.enable_ingestion_job
  log_group_name_query_web    = module.observability.query_web_log_group_name
  log_group_name_ingestion    = module.observability.ingestion_log_group_name
  aws_region                  = var.aws_region
  prometheus_remote_write_url = module.observability.amp_remote_write_url
  opensearch_endpoint         = module.data_services.opensearch_endpoint
  s3_bucket_name              = module.data_services.s3_bucket_name
  dynamodb_table_name         = module.data_services.dynamodb_table_name
  search_index_name           = var.search_index_name
  controls_index_name         = var.controls_index_name
  bedrock_model_id            = var.bedrock_model_id
  bedrock_embedding_model_id  = var.bedrock_embedding_model_id
  app_secrets_secret_arn      = module.app_secrets.secret_arn
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
