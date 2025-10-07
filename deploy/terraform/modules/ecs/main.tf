# =============================================================================
# ECS Module - Main Infrastructure Resources
# Error Triage → Jira Upserter Service
# =============================================================================
# This module provisions AWS ECS Fargate infrastructure for the Error Triage
# service, including:
# - ECS Cluster with Container Insights
# - Task Definition with container configuration
# - ECS Service with load balancer integration
# - Deployment configuration for zero-downtime updates
# =============================================================================

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

# Get current AWS region for dynamic configuration
data "aws_region" "current" {}

# -----------------------------------------------------------------------------
# ECS Cluster
# -----------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = "jiratest-error-triage-${var.environment}"

  # Enable Container Insights for enhanced CloudWatch metrics collection
  # Provides CPU, memory, network, and storage metrics per Section 8.3.2.4
  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name        = "jiratest-error-triage-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# ECS Task Definition
# -----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "app" {
  family = "jiratest-error-triage-${var.environment}"

  # Fargate launch type for serverless container execution
  requires_compatibilities = ["FARGATE"]

  # awsvpc network mode provides dedicated ENI per task (Section 8.3.2.1)
  network_mode = "awsvpc"

  # CPU and memory allocation (environment-specific via variables)
  # dev: 256 CPU / 512 MB, staging/prod: 512 CPU / 1024 MB
  cpu    = var.task_cpu
  memory = var.task_memory

  # IAM roles for ECS infrastructure and application runtime
  execution_role_arn = var.task_execution_role_arn # For pulling images, secrets, logs
  task_role_arn      = var.task_role_arn           # For application AWS API calls

  # Container definitions using jsonencode for proper formatting
  container_definitions = jsonencode([
    {
      name      = var.container_name
      image     = "${var.ecr_repository_url}:${var.container_image_tag}"
      essential = true

      # Port mapping for Gunicorn WSGI server
      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]

      # Environment variables for application configuration
      environment = [
        {
          name  = "ENVIRONMENT"
          value = var.environment
        },
        {
          name  = "REDIS_HOST"
          value = var.redis_endpoint
        },
        {
          name  = "LOG_LEVEL"
          value = "INFO"
        }
      ]

      # Secrets from AWS Secrets Manager for secure credential injection
      # Per Section 8.3.2.3 and 8.3.6, secrets are loaded at task startup
      secrets = [
        {
          name      = "JIRA_BASE_URL"
          valueFrom = "${var.jira_credentials_secret_arn}:base_url::"
        },
        {
          name      = "JIRA_EMAIL"
          valueFrom = "${var.jira_credentials_secret_arn}:email::"
        },
        {
          name      = "JIRA_API_TOKEN"
          valueFrom = "${var.jira_credentials_secret_arn}:api_token::"
        },
        {
          name      = "VERCEL_WEBHOOK_SECRET"
          valueFrom = "${var.webhook_secret_arn}:vercel_secret::"
        },
        {
          name      = "GCP_AUDIENCE"
          valueFrom = "${var.webhook_secret_arn}:gcp_audience::"
        },
        {
          name      = "MONGODB_URI"
          valueFrom = var.mongodb_connection_string_secret_arn
        }
      ]

      # CloudWatch Logs configuration for structured log streaming
      # Per Section 8.3.2.4, logs are sent to environment-specific log group
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "error-triage"
        }
      }

      # Container health check using /healthz endpoint
      # Per Section 8.3.4.2, checks Redis, MongoDB, and Jira connectivity
      healthCheck = {
        command = [
          "CMD-SHELL",
          "curl -f http://localhost:${var.container_port}/healthz || exit 1"
        ]
        interval    = 30  # Check every 30 seconds
        timeout     = 5   # Wait 5 seconds for response
        retries     = 2   # Mark unhealthy after 2 failures
        startPeriod = 60  # Grace period for application initialization
      }
    }
  ])

  tags = {
    Name        = "jiratest-error-triage-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# ECS Service
# -----------------------------------------------------------------------------

resource "aws_ecs_service" "main" {
  name    = "jiratest-error-triage-${var.environment}"
  cluster = aws_ecs_cluster.main.id

  # Reference task definition (includes revision number)
  task_definition = aws_ecs_task_definition.app.arn

  # Number of tasks to maintain (can be overridden by autoscaling)
  desired_count = var.desired_count

  # Fargate launch type for serverless execution
  launch_type = "FARGATE"

  # Platform version 1.4.0 (LATEST) for newest Fargate features
  platform_version = "LATEST"

  # Deployment configuration for rolling updates (Section 8.3.2.1)
  # - deployment_maximum_percent: 200 allows doubling capacity during deployment
  # - deployment_minimum_healthy_percent: 50 ensures half capacity maintained
  deployment_maximum_percent         = var.deployment_maximum_percent
  deployment_minimum_healthy_percent = var.deployment_minimum_healthy_percent

  # Network configuration for awsvpc mode
  network_configuration {
    # Tasks run in private subnets (no direct internet access)
    assign_public_ip = false

    # Subnets for task placement (multi-AZ for high availability)
    subnets = var.private_subnet_ids

    # Security group for task ENIs (allows traffic from ALB)
    security_groups = [var.ecs_security_group_id]
  }

  # Load balancer integration for webhook ingress
  # ALB forwards HTTPS traffic to container port 8000
  load_balancer {
    target_group_arn = var.alb_target_group_arn
    container_name   = var.container_name
    container_port   = var.container_port
  }

  # Health check grace period (Section 8.3.4.2)
  # Allows 60 seconds for application initialization before health checks
  health_check_grace_period_seconds = var.health_check_grace_period_seconds

  # Deployment circuit breaker with automatic rollback
  # Detects failed deployments and reverts to previous task definition
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # ECS managed tags for resource tracking
  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  tags = {
    Name        = "jiratest-error-triage-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
  }

  # Ensure service is created only after load balancer target group exists
  depends_on = [var.alb_target_group_arn]
}
