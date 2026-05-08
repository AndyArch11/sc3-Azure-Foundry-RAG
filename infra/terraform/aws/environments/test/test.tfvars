aws_region       = "ap-southeast-2"
aws_region_short = "apse2"
project          = "rag"
environment      = "test"

vpc_cidr             = "10.31.0.0/16"
private_subnet_cidrs = ["10.31.1.0/24", "10.31.2.0/24"]
public_subnet_cidrs  = ["10.31.101.0/24", "10.31.102.0/24"]

opensearch_engine_version             = "OpenSearch_2.13"
opensearch_instance_type              = "r6g.large.search"
opensearch_instance_count             = 1
opensearch_volume_size_gb             = 20
ensure_opensearch_service_linked_role = false

search_index_name   = "grounding-index"
controls_index_name = "controls-index"

bedrock_model_id           = "anthropic.claude-3-5-sonnet-20241022-v2:0"
bedrock_embedding_model_id = "amazon.titan-embed-text-v2:0"

query_web_cpu           = 512
query_web_memory_mb     = 1024
query_web_desired_count = 1
enable_query_web        = false
enable_ingestion_job    = false

query_web_image_tag = "latest" # Set to an immutable tag during deployment.
ingestion_image_tag = "latest" # Set to an immutable tag during deployment.

