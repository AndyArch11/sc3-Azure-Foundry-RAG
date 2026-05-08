aws_region       = "ap-southeast-2"
aws_region_short = "apse2"
project          = "rag"
environment      = "prod"

vpc_cidr             = "10.32.0.0/16"
private_subnet_cidrs = ["10.32.1.0/24", "10.32.2.0/24", "10.32.3.0/24"]
public_subnet_cidrs  = ["10.32.101.0/24", "10.32.102.0/24", "10.32.103.0/24"]

opensearch_engine_version             = "OpenSearch_2.13"
opensearch_instance_type              = "r6g.xlarge.search"
opensearch_instance_count             = 3 # Multi-AZ cluster
opensearch_volume_size_gb             = 100
ensure_opensearch_service_linked_role = false

search_index_name   = "grounding-index"
controls_index_name = "controls-index"

bedrock_model_id           = "anthropic.claude-3-5-sonnet-20241022-v2:0"
bedrock_embedding_model_id = "amazon.titan-embed-text-v2:0"

query_web_cpu           = 1024
query_web_memory_mb     = 2048
query_web_desired_count = 2
enable_query_web        = false # Set true and supply an immutable image tag at deployment time.
enable_ingestion_job    = false

query_web_image_tag = "latest" # Set to an immutable tag during deployment.
ingestion_image_tag = "latest" # Set to an immutable tag during deployment.

