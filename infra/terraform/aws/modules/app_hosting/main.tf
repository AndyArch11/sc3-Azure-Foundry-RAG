resource "aws_ecs_cluster" "this" {
  name = "ecs-${var.naming_suffix}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "ecs-${var.naming_suffix}" }
}

locals {
  query_web_ingress_enabled   = var.enable_query_web && var.query_web_ingress_mode != "none"
  query_web_public_ingress    = var.query_web_ingress_mode == "public"
  query_web_https_enabled     = local.query_web_ingress_enabled && var.query_web_tls_certificate_arn != ""
  query_web_alb_subnet_ids    = local.query_web_public_ingress ? var.public_subnet_ids : var.private_subnet_ids
  query_web_alb_ingress_cidrs = local.query_web_public_ingress ? var.query_web_public_ingress_cidrs : [var.vpc_cidr]
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  count = var.enable_cluster_capacity_providers ? 1 : 0

  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_security_group" "query_web_alb" {
  count = local.query_web_ingress_enabled ? 1 : 0

  name        = "alb-query-web-${var.naming_suffix}"
  description = "ALB ingress for query-web."
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = local.query_web_alb_ingress_cidrs
    description = local.query_web_public_ingress ? "HTTP from allowed public CIDRs" : "HTTP from VPC CIDR"
  }

  dynamic "ingress" {
    for_each = local.query_web_https_enabled ? [1] : []
    content {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = local.query_web_alb_ingress_cidrs
      description = local.query_web_public_ingress ? "HTTPS from allowed public CIDRs" : "HTTPS from VPC CIDR"
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow outbound to ECS targets"
  }

  tags = { Name = "alb-query-web-${var.naming_suffix}" }
}

resource "aws_security_group_rule" "ecs_from_query_web_alb" {
  count = local.query_web_ingress_enabled ? 1 : 0

  type                     = "ingress"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  security_group_id        = var.ecs_sg_id
  source_security_group_id = aws_security_group.query_web_alb[0].id
  description              = "Allow query-web ALB to reach ECS task port 8080"
}

resource "aws_lb" "query_web" {
  count = local.query_web_ingress_enabled ? 1 : 0

  name               = substr("alb-qw-${var.naming_suffix}", 0, 32)
  internal           = !local.query_web_public_ingress
  load_balancer_type = "application"
  security_groups    = [aws_security_group.query_web_alb[0].id]
  subnets            = local.query_web_alb_subnet_ids

  lifecycle {
    precondition {
      condition     = !(var.environment == "prod" && var.query_web_ingress_mode == "public")
      error_message = "query_web_ingress_mode=public is not allowed when environment=prod."
    }

    precondition {
      condition     = var.query_web_ingress_mode != "public" || length(var.query_web_public_ingress_cidrs) > 0
      error_message = "query_web_public_ingress_cidrs must be set when query_web_ingress_mode=public."
    }
  }

  tags = { Name = "alb-qw-${var.naming_suffix}" }
}

resource "aws_lb_target_group" "query_web" {
  count = local.query_web_ingress_enabled ? 1 : 0

  name        = substr("tg-qw-${var.naming_suffix}", 0, 32)
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }

  tags = { Name = "tg-qw-${var.naming_suffix}" }
}

resource "aws_lb_listener" "query_web_http" {
  count = local.query_web_ingress_enabled ? 1 : 0

  load_balancer_arn = aws_lb.query_web[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = local.query_web_https_enabled ? "redirect" : "forward"

    dynamic "redirect" {
      for_each = local.query_web_https_enabled ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    target_group_arn = local.query_web_https_enabled ? null : aws_lb_target_group.query_web[0].arn
  }
}

resource "aws_lb_listener" "query_web_https" {
  count = local.query_web_https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.query_web[0].arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.query_web_tls_certificate_arn
  ssl_policy        = var.query_web_tls_ssl_policy

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.query_web[0].arn
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
        { name = "CLOUD_PROVIDER", value = "aws" },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "OPENSEARCH_ENDPOINT", value = var.opensearch_endpoint },
        { name = "SEARCH_INDEX_NAME", value = var.search_index_name },
        { name = "CONTROLS_INDEX_NAME", value = var.controls_index_name },
        { name = "S3_BUCKET_NAME", value = var.s3_bucket_name },
        { name = "DYNAMODB_TABLE", value = var.dynamodb_table_name },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "BEDROCK_EMBEDDING_MODEL_ID", value = var.bedrock_embedding_model_id },
        # ECS run-task parameters so query-web can trigger the ingestion task from the browser
        { name = "ECS_CLUSTER_NAME", value = aws_ecs_cluster.this.name },
        { name = "INGESTION_TASK_DEFINITION_ARN", value = var.enable_ingestion_job ? aws_ecs_task_definition.ingestion[0].arn : "" },
        { name = "ECS_SG_ID", value = var.ecs_sg_id },
        { name = "ECS_SUBNET_ID", value = length(var.private_subnet_ids) > 0 ? var.private_subnet_ids[0] : "" },
      ]

      # Inject secrets from Secrets Manager at task start via the execution role.
      # The JSON key path format is: <secret-arn>:<json-key>::
      secrets = var.app_secrets_secret_arn != "" ? [
        {
          name      = "QUERY_WEB_AUTH_TOKEN"
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
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
    ], (var.enable_adot_sidecar && var.prometheus_remote_write_url != "") ? [
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

  dynamic "load_balancer" {
    for_each = local.query_web_ingress_enabled ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.query_web[0].arn
      container_name   = "query-web"
      container_port   = 8080
    }
  }

  # Prevent Terraform from resetting desired_count during auto-scaling.
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.query_web_http, aws_lb_listener.query_web_https]

  tags = { Name = "svc-query-web-${var.naming_suffix}" }
}

# ── confluence poller service ────────────────────────────────────────────────

resource "aws_ecs_task_definition" "confluence_poller" {
  count = var.enable_confluence_poller_service ? 1 : 0

  family                   = "td-confluence-poller-${var.naming_suffix}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.confluence_poller_cpu)
  memory                   = tostring(var.confluence_poller_memory_mb)
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name       = "confluence-poller"
      image      = "${var.confluence_poller_repository_url}:${var.confluence_poller_image_tag}"
      essential  = true
      entryPoint = ["python", "-m", "runtime.assessment_orchestration.polling_worker_main"]

      environment = concat([
        { name = "CLOUD_PROVIDER", value = "aws" },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "DYNAMODB_TABLE", value = var.dynamodb_table_name },
        { name = "S3_BUCKET_NAME", value = var.s3_bucket_name },
        { name = "OPENSEARCH_ENDPOINT", value = var.opensearch_endpoint },
        { name = "SEARCH_INDEX_NAME", value = var.search_index_name },
        { name = "CONTROLS_INDEX_NAME", value = var.controls_index_name },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "BEDROCK_EMBEDDING_MODEL_ID", value = var.bedrock_embedding_model_id },
        { name = "CONFLUENCE_BASE_URL", value = var.confluence_base_url },
        { name = "CONFLUENCE_AUTH_MODE", value = var.confluence_auth_mode },
        { name = "CONFLUENCE_AUTH_EMAIL", value = var.confluence_auth_email },
        { name = "CONFLUENCE_MENTION_ALIASES", value = join(",", var.confluence_mention_aliases) },
        { name = "CONFLUENCE_POLL_INTERVAL_SECONDS", value = tostring(var.confluence_poll_interval_seconds) },
        { name = "CONFLUENCE_POLL_LEASE_TTL_SECONDS", value = tostring(var.confluence_poll_lease_ttl_seconds) },
        { name = "CONFLUENCE_POLL_MAX_EVENT_ATTEMPTS", value = tostring(var.confluence_poll_max_event_attempts) },
        { name = "CONFLUENCE_POLL_INITIAL_LOOKBACK", value = var.confluence_poll_initial_lookback },
        { name = "CONFLUENCE_POLL_DRY_RUN", value = tostring(var.confluence_poll_dry_run) },
        ], trimspace(var.confluence_cloud_id) != "" ? [
        { name = "CONFLUENCE_CLOUD_ID", value = var.confluence_cloud_id },
        ] : [], trimspace(var.confluence_account_id) != "" ? [
        { name = "CONFLUENCE_ACCOUNT_ID", value = var.confluence_account_id },
        ] : [], length(var.confluence_poll_space_keys) > 0 ? [
        { name = "CONFLUENCE_POLL_SPACE_KEYS", value = join(",", var.confluence_poll_space_keys) },
      ] : [])

      secrets = var.app_secrets_secret_arn != "" ? [
        {
          name      = "CONFLUENCE_API_TOKEN"
          valueFrom = "${var.app_secrets_secret_arn}:confluence_api_token::"
        },
      ] : []

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name_confluence_poller
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "confluence-poller"
        }
      }
    }
  ])

  tags = { Name = "td-confluence-poller-${var.naming_suffix}" }
}

resource "aws_ecs_service" "confluence_poller" {
  count = var.enable_confluence_poller_service ? 1 : 0

  name            = "svc-confluence-poller-${var.naming_suffix}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.confluence_poller[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_sg_id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = { Name = "svc-confluence-poller-${var.naming_suffix}" }
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
        { name = "CLOUD_PROVIDER", value = "aws" },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "S3_BUCKET_NAME", value = var.s3_bucket_name },
        { name = "OPENSEARCH_ENDPOINT", value = var.opensearch_endpoint },
        { name = "SEARCH_INDEX_NAME", value = var.search_index_name },
        { name = "CONTROLS_INDEX_NAME", value = var.controls_index_name },
        { name = "DYNAMODB_TABLE", value = var.dynamodb_table_name },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
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
