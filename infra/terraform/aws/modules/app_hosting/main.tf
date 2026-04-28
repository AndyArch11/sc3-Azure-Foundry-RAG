resource "aws_ecs_cluster" "this" {
  name = "ecs-${var.naming_suffix}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "ecs-${var.naming_suffix}" }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ── query_web service ─────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "query_web" {
  count = var.enable_query_web ? 1 : 0

  family                   = "td-query-web-${var.naming_suffix}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.query_web_cpu)
  memory                   = tostring(var.query_web_memory_mb)
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode(concat([
    {
      name      = "query-web"
      image     = "${var.query_web_repository_url}:${var.query_web_image_tag}"
      essential = true

      portMappings = [{ containerPort = 8080, protocol = "tcp" }]

      environment = [
        { name = "CLOUD_PROVIDER",             value = "aws" },
        { name = "AWS_REGION",                 value = var.aws_region },
        { name = "OPENSEARCH_ENDPOINT",        value = var.opensearch_endpoint },
        { name = "SEARCH_INDEX_NAME",          value = var.search_index_name },
        { name = "CONTROLS_INDEX_NAME",        value = var.controls_index_name },
        { name = "S3_BUCKET_NAME",             value = var.s3_bucket_name },
        { name = "DYNAMODB_TABLE",             value = var.dynamodb_table_name },
        { name = "BEDROCK_MODEL_ID",           value = var.bedrock_model_id },
        { name = "BEDROCK_EMBEDDING_MODEL_ID", value = var.bedrock_embedding_model_id },
      ]

      # Inject secrets from Secrets Manager at task start via the execution role.
      # The JSON key path format is: <secret-arn>:<json-key>::
      secrets = var.app_secrets_secret_arn != "" ? [
        {
          name      = "AUTH_TOKEN"
          valueFrom = "${var.app_secrets_secret_arn}:auth_token::"
        },
      ] : []

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name_query_web
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "query-web"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -sf http://localhost:8080/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ], var.prometheus_remote_write_url != "" ? [
    {
      name       = "adot-collector"
      image      = "public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0"
      essential  = false
      entryPoint = ["/bin/sh", "-lc"]
      command = [<<-EOT
cat >/tmp/otel-config.yaml <<'YAML'
receivers:
  prometheus:
    config:
      scrape_configs:
        - job_name: query-web
          scrape_interval: 15s
          static_configs:
            - targets: ['127.0.0.1:8080']
processors:
  batch: {}
extensions:
  sigv4auth:
    region: ${var.aws_region}
exporters:
  prometheusremotewrite:
    endpoint: ${var.prometheus_remote_write_url}
    auth:
      authenticator: sigv4auth
service:
  extensions: [sigv4auth]
  pipelines:
    metrics:
      receivers: [prometheus]
      processors: [batch]
      exporters: [prometheusremotewrite]
YAML
/awscollector --config=/tmp/otel-config.yaml
EOT
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name_query_web
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "adot"
        }
      }
    }
  ] : []))

  tags = { Name = "td-query-web-${var.naming_suffix}" }
}

resource "aws_ecs_service" "query_web" {
  count = var.enable_query_web ? 1 : 0

  name            = "svc-query-web-${var.naming_suffix}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.query_web[0].arn
  desired_count   = var.query_web_desired_count
  launch_type     = "FARGATE"

  # Allow Fargate to replace tasks without waiting for draining.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_sg_id]
    assign_public_ip = false
  }

  # Prevent Terraform from resetting desired_count during auto-scaling.
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = { Name = "svc-query-web-${var.naming_suffix}" }
}

# ── ingestion scheduled task ──────────────────────────────────────────────────

resource "aws_ecs_task_definition" "ingestion" {
  count = var.enable_ingestion_job ? 1 : 0

  family                   = "td-ingestion-${var.naming_suffix}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.ingestion_cpu)
  memory                   = tostring(var.ingestion_memory_mb)
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "ingestion"
      image     = "${var.ingestion_repository_url}:${var.ingestion_image_tag}"
      essential = true

      environment = [
        { name = "CLOUD_PROVIDER",             value = "aws" },
        { name = "AWS_REGION",                 value = var.aws_region },
        { name = "S3_BUCKET_NAME",             value = var.s3_bucket_name },
        { name = "OPENSEARCH_ENDPOINT",        value = var.opensearch_endpoint },
        { name = "SEARCH_INDEX_NAME",          value = var.search_index_name },
        { name = "CONTROLS_INDEX_NAME",        value = var.controls_index_name },
        { name = "DYNAMODB_TABLE",             value = var.dynamodb_table_name },
        { name = "BEDROCK_MODEL_ID",           value = var.bedrock_model_id },
        { name = "BEDROCK_EMBEDDING_MODEL_ID", value = var.bedrock_embedding_model_id },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name_ingestion
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ingestion"
        }
      }
    }
  ])

  tags = { Name = "td-ingestion-${var.naming_suffix}" }
}
