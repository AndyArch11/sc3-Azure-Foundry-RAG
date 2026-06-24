aws_region       = "ap-southeast-2"
aws_region_short = "apse2"
project          = "rag"
environment      = "dev"
dynamodb_table_name = "rag-state-rag-dev-apse2-v2"

vpc_cidr             = "10.30.0.0/16"
private_subnet_cidrs = ["10.30.1.0/24", "10.30.2.0/24"]
public_subnet_cidrs  = ["10.30.101.0/24", "10.30.102.0/24"]

opensearch_engine_version             = "OpenSearch_2.13" # OpenSearch 2.13+ is required for vector search capabilities.
opensearch_instance_type              = "r6g.large.search" # r6g.large.search is a good balance of cost and performance for development/testing. Consider scaling up for production or larger workloads (e.g. r6g.xlarge.search or larger).
opensearch_instance_count             = 1
opensearch_volume_size_gb             = 20
ensure_opensearch_service_linked_role = false # Set to true if you want Terraform to create the AWSServiceRoleForAmazonOpenSearchService service-linked role, which is required for OpenSearch domains to write to CloudWatch Logs. If you set this to false, ensure the role already exists in your account before applying.

search_index_name   = "grounding-index"
controls_index_name = "controls-index"

# Grounding index/vector controls (safe defaults).
# Set opensearch_grounding_index_knn_enabled=true and grounding_embed_on_ingest=true
# when recreating grounding-index as knn_vector and reingesting chunk embeddings.
opensearch_grounding_index_knn_enabled   = true # Set to true to enable KNN vector search on the grounding index (note: this will require reindexing the grounding index with a knn_vector field and reingesting all chunk embeddings).
opensearch_grounding_embedding_dimensions = 1024 # Number of dimensions for vector embeddings stored in OpenSearch. Must match the output dimensions of the embedding model configured in Bedrock (e.g. amazon.titan-embed-text-v2 outputs 1024-dimensional embeddings). If you change this value, you will need to reindex the grounding index with a knn_vector field that has the updated number of dimensions, and reingest all chunk embeddings.
grounding_embed_on_ingest                = true # Set to true to automatically generate and store vector embeddings for document chunks at ingest time (requires opensearch_grounding_embedding_dimensions to be set, and Bedrock embedding model to be configured). If false, embeddings will need to be generated and ingested separately (e.g. through a custom Lambda function or external process).

# Mantle models require using Bedrock Mantle which batches and processes requests asynchronously — this is suitable for longer-running tasks like document ingestion, but not for real-time question answering.
# bedrock_model_id           = "mistral.mistral-large-3-675b-instruct" # Strong general-purpose model, good for both question answering and document ingestion, but one of the more expensive options.
bedrock_model_id           = "qwen.qwen3-32b" # for a budget option
# Runtime models can be used with Bedrock API calls in real-time (e.g. for question answering)
# bedrock_model_id           = "amazon.nova-pro-v1:0" 
bedrock_embedding_model_id = "amazon.titan-embed-text-v2:0"
bedrock_api_mode           = "mantle" # Set to "runtime" for real-time API calls, or "mantle" to use Bedrock Mantle for asynchronous processing (requires initial_bedrock_mantle_api_key to be set).
# initial_bedrock_mantle_api_key = "<set-me-if-using-mantle>"

enable_query_web                 = true # Set to false to disable deployment of the query web service (e.g. for cost savings in development or if you only want to deploy the ingestion job).
query_web_cpu                    = 512
query_web_memory_mb              = 1024
query_web_desired_count          = 1
query_web_ingress_mode           = "public"              # auto|none|internal|public ; prod auto resolves to internal
query_web_public_ingress_cidrs   = ["101.161.226.47/32"] # required only when query_web_ingress_mode = "public", allow list of CIDR blocks that can access the service if using public ingress
# query_web_public_ingress_cidrs = ["203.0.113.0/24"] # required only when query_web_ingress_mode = "public", allow list of CIDR blocks that can access the service if using public ingress
# query_web_tls_certificate_arn = "arn:aws:acm:ap-southeast-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"
# query_web_tls_ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"

enable_ingestion_job             = true

enable_confluence_poller_service = false # Set to true to enable the Confluence poller service, which ingests content from Confluence Cloud. Requires additional Confluence-related variables to be set (e.g. confluence_base_url, confluence_auth_mode, etc.).
confluence_poller_cpu            = 512
confluence_poller_memory_mb      = 1024
# Confluence poller settings
# confluence_base_url = "https://<org>.atlassian.net"
# confluence_auth_mode = "basic"
# confluence_auth_email = "service-account@example.com"
# confluence_cloud_id = "<set-me-optional>"
# confluence_account_id = "<set-me-account-id>"
# confluence_poll_space_keys = ["SEC", "COMP"]
# confluence_poll_interval_seconds = 75
# confluence_poll_lease_ttl_seconds = 300
# confluence_poll_max_event_attempts = 3
# confluence_poll_initial_lookback = "PT1H"
# confluence_poll_dry_run = true

# initial_confluence_api_token = "<set-me>"

ingestion_image_tag = "202606241435-9d0c81d"
query_web_image_tag = "202606241435-9d0c81d"
confluence_poller_image_tag = "202605111406-be80d2b"