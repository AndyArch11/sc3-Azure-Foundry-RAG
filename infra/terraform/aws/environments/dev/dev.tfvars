aws_region       = "ap-southeast-2"
aws_region_short = "apse2"
project          = "rag"
environment      = "dev"
dynamodb_table_name = "rag-state-rag-dev-apse2-v2"

vpc_cidr             = "10.30.0.0/16"
private_subnet_cidrs = ["10.30.1.0/24", "10.30.2.0/24"]
public_subnet_cidrs  = ["10.30.101.0/24", "10.30.102.0/24"]

opensearch_engine_version             = "OpenSearch_2.13"
opensearch_instance_type              = "r6g.large.search"
opensearch_instance_count             = 1
opensearch_volume_size_gb             = 20
ensure_opensearch_service_linked_role = false

search_index_name   = "grounding-index"
controls_index_name = "controls-index"

# Mantle models require using Bedrock Mantle which batches and processes requests asynchronously — this is suitable for longer-running tasks like document ingestion, but not for real-time question answering.
# bedrock_model_id           = "mistral.mistral-large-3-675b-instruct" # Strong general-purpose model, good for both question answering and document ingestion, but one of the more expensive options.
bedrock_model_id           = "qwen.qwen3-32b" # for a budget option
# Runtime models can be used with Bedrock API calls in real-time (e.g. for question answering)
# bedrock_model_id           = "amazon.nova-pro-v1:0" 
bedrock_embedding_model_id = "amazon.titan-embed-text-v2:0"
bedrock_api_mode           = "mantle" # Set to "runtime" for real-time API calls, or "mantle" to use Bedrock Mantle for asynchronous processing (requires initial_bedrock_mantle_api_key to be set).
# initial_bedrock_mantle_api_key = "<set-me-if-using-mantle>"

enable_query_web                 = true
query_web_cpu                    = 512
query_web_memory_mb              = 1024
query_web_desired_count          = 1
query_web_ingress_mode           = "public"              # auto|none|internal|public ; prod auto resolves to internal
query_web_public_ingress_cidrs   = ["101.161.226.47/32"] # required only when query_web_ingress_mode = "public", allow list of CIDR blocks that can access the service if using public ingress
# query_web_public_ingress_cidrs = ["203.0.113.0/24"] # required only when query_web_ingress_mode = "public", allow list of CIDR blocks that can access the service if using public ingress
# query_web_tls_certificate_arn = "arn:aws:acm:ap-southeast-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"
# query_web_tls_ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"

enable_ingestion_job             = true

enable_confluence_poller_service = false
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

ingestion_image_tag         = "202606211146-2a2de06"
query_web_image_tag         = "202606211147-2a2de06"
confluence_poller_image_tag = "202605111406-be80d2b"