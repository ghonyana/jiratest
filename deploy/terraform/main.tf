# =============================================================================
# Error Triage → Jira Upserter Service - Terraform Root Module
# =============================================================================
# Purpose: Orchestrate infrastructure provisioning for Error Triage service
#          through modular composition of AWS resources (ECS Fargate, ElastiCache
#          Redis, Application Load Balancer, IAM roles, Secrets Manager)
#
# Architecture: Multi-AZ deployment across 3 availability zones in us-east-1
#               with stateless ECS Fargate tasks, managed Redis caching layer,
#               Application Load Balancer for webhook ingress, and centralized
#               secrets management for Jira credentials and webhook authentication
#
# Dependencies: Requires existing VPC (jiratest-prod-vpc), subnets across 3 AZs,
#               and ACM wildcard certificate (*.jiratest.com)
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform and Provider Configuration
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state backend configuration for S3 with DynamoDB locking
  # State file path pattern: error-triage/{environment}/terraform.tfstate
  # Enables multi-environment isolation and prevents concurrent modifications
  backend "s3" {
    bucket         = "jiratest-terraform-state"
    key            = "error-triage/${var.environment}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    kms_key_id     = "alias/terraform-state-key"
    dynamodb_table = "jiratest-terraform-locks"

    # Enable versioning for state file history and rollback capability
    # Configure lifecycle rules in S3 bucket for 90-day version retention
  }
}

# AWS Provider configuration with default resource tags for cost allocation
# All resources inherit these tags unless explicitly overridden
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      Service     = "error-triage"
      Project     = "jiratest"
      Team        = "platform-engineering"
      CostCenter  = "infrastructure"
      ManagedBy   = "terraform"
      Repository  = "jiratest/error-triage-jira-upserter"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources - Existing Infrastructure Discovery
# -----------------------------------------------------------------------------

# Existing VPC lookup for jiratest-prod-vpc (10.0.0.0/16 CIDR)
# Reuses organizational VPC infrastructure for network isolation
data "aws_vpc" "jiratest_vpc" {
  filter {
    name   = "tag:Name"
    values = ["jiratest-prod-vpc"]
  }

  filter {
    name   = "cidr-block"
    values = ["10.0.0.0/16"]
  }
}

# Public subnets for Application Load Balancer deployment (3 AZs)
# ALB receives inbound HTTPS webhooks from Vercel and GCP platforms
# Subnet CIDR blocks: 10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.jiratest_vpc.id]
  }

  filter {
    name   = "tag:Tier"
    values = ["public"]
  }

  filter {
    name   = "tag:Environment"
    values = [var.environment]
  }
}

# Private subnets for ECS Fargate tasks (3 AZs)
# Application containers with no direct internet access, routed via NAT Gateway
# Subnet CIDR blocks: 10.0.11.0/24, 10.0.12.0/24, 10.0.13.0/24
data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.jiratest_vpc.id]
  }

  filter {
    name   = "tag:Tier"
    values = ["private"]
  }

  filter {
    name   = "tag:Environment"
    values = [var.environment]
  }
}

# Data tier subnets for ElastiCache Redis cluster (3 AZs)
# Isolated data plane accessible only from ECS task security group
# Subnet CIDR blocks: 10.0.21.0/24, 10.0.22.0/24, 10.0.23.0/24
data "aws_subnets" "data" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.jiratest_vpc.id]
  }

  filter {
    name   = "tag:Tier"
    values = ["data"]
  }

  filter {
    name   = "tag:Environment"
    values = [var.environment]
  }
}

# ACM certificate for TLS termination at Application Load Balancer
# Wildcard certificate: *.jiratest.com with automatic 60-day pre-expiration renewal
# ALB security policy: ELBSecurityPolicy-TLS-1-2-2017-01 (TLS 1.2+ minimum)
data "aws_acm_certificate" "jiratest_wildcard" {
  domain   = "*.jiratest.com"
  statuses = ["ISSUED"]
  most_recent = true
}

# Retrieve availability zones for multi-AZ distribution
# Used for resource placement across us-east-1a, us-east-1b, us-east-1c
data "aws_availability_zones" "available" {
  state = "available"

  # Limit to first 3 AZs for consistent multi-AZ deployment
  filter {
    name   = "zone-name"
    values = ["us-east-1a", "us-east-1b", "us-east-1c"]
  }
}

# -----------------------------------------------------------------------------
# Module: IAM Roles and Policies
# -----------------------------------------------------------------------------
# Creates ECS task execution role (infrastructure operations) and task role
# (application runtime permissions). Follows principle of least privilege with
# separate roles for container launch operations versus application API access.
# -----------------------------------------------------------------------------

module "iam" {
  source = "./modules/iam"

  environment    = var.environment
  service_name   = "error-triage"
  aws_region     = var.aws_region
  aws_account_id = data.aws_caller_identity.current.account_id

  # Secret ARN patterns for Secrets Manager access control
  # Task execution role retrieves secrets during container launch
  jira_secret_arn_pattern    = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:jira/jiratest/*"
  mongodb_secret_arn_pattern = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:mongodb/jiratest/*"
  redis_secret_arn_pattern   = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:redis/jiratest/*"

  # CloudWatch Logs group for application log streaming
  # Task role grants logs:PutLogEvents permission for runtime log emission
  cloudwatch_log_group_arn = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ecs/jiratest-error-triage-${var.environment}:*"

  # ECR repository for container image pulls during task launch
  ecr_repository_arn = "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/jiratest-error-triage"

  tags = {
    Module = "iam"
  }
}

# Retrieve AWS account ID for ARN construction
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Module: Secrets Manager
# -----------------------------------------------------------------------------
# Defines secrets for Jira API credentials, webhook signature secrets, MongoDB
# connection strings, and Redis connection details. All secrets encrypted with
# AWS KMS customer managed keys (AES-256-GCM) with automatic annual key rotation.
# -----------------------------------------------------------------------------

module "secrets" {
  source = "./modules/secrets"

  environment  = var.environment
  service_name = "error-triage"
  aws_region   = var.aws_region

  # KMS key ID for secret encryption at rest (FIPS 140-2 validated)
  # Automatic annual key rotation enabled per security policy
  kms_key_id = var.kms_key_id

  # Secret rotation policies
  # Jira API tokens: manual 90 days (Atlassian limitation)
  # Webhook secrets: manual 180 days (requires Vercel config sync)
  # MongoDB passwords: automatic 90 days via Lambda rotation function
  enable_mongodb_rotation = var.enable_mongodb
  mongodb_rotation_days   = 90
  jira_rotation_days      = 90  # Manual rotation reminder
  webhook_rotation_days   = 180 # Manual rotation reminder

  # Secret version retention for rollback capability
  # 30 days of versions retained for failed rotation recovery
  secret_version_retention_days = 30

  tags = {
    Module = "secrets"
  }

  # Explicit dependency: IAM roles must exist before secret access policies
  depends_on = [module.iam]
}

# -----------------------------------------------------------------------------
# Module: ElastiCache Redis
# -----------------------------------------------------------------------------
# Provisions Redis cluster for frequency counters (5-min rolling windows),
# event deduplication cache (1-hour TTL), and comment rate limiting (15-min TTL).
# Multi-AZ deployment for production with automatic failover (60-90 seconds).
# -----------------------------------------------------------------------------

module "redis" {
  source = "./modules/redis"

  environment  = var.environment
  service_name = "error-triage"
  vpc_id       = data.aws_vpc.jiratest_vpc.id

  # Subnet group for Redis cluster deployment in data tier subnets
  # Multi-AZ placement across 3 availability zones for high availability
  subnet_ids = data.aws_subnets.data.ids

  # Environment-specific node types per Section 8.3.2.2
  # Development: cache.t4g.micro (0.5 GB, single-node)
  # Staging: cache.t4g.small (0.5 GB, single-node)
  # Production: cache.t4g.medium (1.2 GB, primary + replica)
  node_type = var.redis_node_type

  # Multi-AZ configuration: enabled for production only
  # Automatic failover promotes replica to primary on failure
  # DNS endpoint updated automatically, application reconnects via retry logic
  num_cache_nodes = var.environment == "production" ? 2 : 1
  multi_az_enabled = var.environment == "production" ? true : false

  # Redis engine version and parameter group configuration
  # Redis 7.2+ with enhanced ACL support, TLS in-transit encryption
  engine_version = "7.2"

  # Persistence configuration for data durability
  # AOF (Append-Only File) with 1-second fsync + RDB snapshots hourly
  # Balances durability with performance for frequency counters and rate limits
  snapshot_retention_limit = var.environment == "production" ? 7 : 1
  snapshot_window          = "03:00-05:00" # UTC maintenance window

  # Security configuration
  # Transit encryption (TLS) enabled for data in transit
  # AUTH token authentication for access control
  # Security group restricts access to ECS task security group only
  transit_encryption_enabled = true
  auth_token_enabled         = true

  # Automatic minor version patching during maintenance window
  # Reduces operational overhead for security updates
  auto_minor_version_upgrade = true
  maintenance_window         = "sun:05:00-sun:07:00" # UTC

  # CloudWatch alarms for operational monitoring
  # Alert on high CPU, memory usage, evictions, connection counts
  enable_cloudwatch_alarms = true
  alarm_cpu_threshold      = 75 # Percentage
  alarm_memory_threshold   = 80 # Percentage

  tags = {
    Module = "redis"
  }

  # Security group IDs passed from ECS module for redis-sg ingress rules
  # Redis security group allows inbound port 6379 from ECS task security group
  # Configuration deferred until ECS module creates task security group
  ecs_security_group_id = module.ecs.ecs_task_security_group_id

  # Explicit dependencies
  depends_on = [module.secrets]
}

# -----------------------------------------------------------------------------
# Module: ECS Fargate Service
# -----------------------------------------------------------------------------
# Deploys containerized Flask application as ECS Fargate service with Application
# Load Balancer for webhook ingress, auto-scaling policies (2-20 tasks), health
# check integration via /healthz endpoint, and CloudWatch Logs for structured
# JSON log streaming. Implements zero-downtime rolling deployments (50% replacement).
# -----------------------------------------------------------------------------

module "ecs" {
  source = "./modules/ecs"

  environment  = var.environment
  service_name = "error-triage"
  aws_region   = var.aws_region
  vpc_id       = data.aws_vpc.jiratest_vpc.id

  # Subnet configuration for multi-AZ deployment
  # ALB in public subnets (internet-facing), ECS tasks in private subnets
  public_subnet_ids  = data.aws_subnets.public.ids
  private_subnet_ids = data.aws_subnets.private.ids

  # Container image configuration
  # ECR repository: jiratest-error-triage
  # Image tag: environment-specific or semantic version from CI/CD
  container_image = var.container_image
  container_port  = 8080 # Gunicorn listens on port 8080

  # ECS task resource allocation per Section 8.3.2.1
  # Development: 0.25 vCPU / 0.5 GB (minimal cost)
  # Staging: 0.5 vCPU / 1 GB (production-like sizing)
  # Production: 0.5 vCPU / 1 GB (baseline for 20 req/s per task)
  task_cpu    = var.ecs_task_cpu
  task_memory = var.ecs_task_memory

  # Service capacity and auto-scaling configuration
  # Baseline task count: 2 (dev), 4 (staging), 6 (production)
  # Auto-scaling: target tracking 70% CPU, max 20 tasks for burst traffic
  desired_count     = var.ecs_desired_count
  min_capacity      = var.ecs_min_capacity
  max_capacity      = var.ecs_max_capacity
  cpu_target_value  = 70 # Target CPU utilization percentage

  # IAM role ARNs from IAM module
  # Task execution role: infrastructure operations (ECR, Secrets Manager)
  # Task role: application runtime permissions (CloudWatch Logs)
  task_execution_role_arn = module.iam.ecs_task_execution_role_arn
  task_role_arn           = module.iam.ecs_task_role_arn

  # Application Load Balancer configuration
  # TLS termination at ALB with ACM certificate (*.jiratest.com)
  # Security policy: ELBSecurityPolicy-TLS-1-2-2017-01 (TLS 1.2+ minimum)
  alb_certificate_arn = data.aws_acm_certificate.jiratest_wildcard.arn
  alb_security_policy = "ELBSecurityPolicy-TLS-1-2-2017-01"

  # Health check configuration for /healthz endpoint
  # Validates dependency connectivity (Redis, MongoDB, Jira)
  # Interval: 30s, timeout: 5s, healthy threshold: 2, unhealthy threshold: 2
  health_check_path                = "/healthz"
  health_check_interval            = 30
  health_check_timeout             = 5
  health_check_healthy_threshold   = 2
  health_check_unhealthy_threshold = 2
  health_check_grace_period        = 60 # Seconds after task launch

  # Deployment strategy for zero-downtime updates
  # Rolling update: 50% replacement rate, 60-second deregistration delay
  # ALB connection draining allows in-flight requests to complete
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200
  deregistration_delay              = 30 # Seconds for connection draining

  # Environment variables injected into container runtime
  # Secrets retrieved from Secrets Manager via task execution role
  environment_variables = {
    ENVIRONMENT       = var.environment
    AWS_REGION        = var.aws_region
    SERVICE_NAME      = "error-triage"
    REDIS_HOST        = module.redis.redis_endpoint
    REDIS_PORT        = "6379"
    REDIS_TLS_ENABLED = "true"
    ENABLE_MONGODB    = tostring(var.enable_mongodb)
    LOG_LEVEL         = var.log_level
    
    # Jira configuration
    JIRA_PROJECT_KEY = var.jira_project_key
    
    # Severity rules and ownership rules file paths (mounted from configuration)
    SEVERITY_RULES_PATH    = "/app/config/severity_rules.yaml"
    OWNERSHIP_RULES_PATH   = "/app/config/ownership_rules.yaml"
    SANITIZATION_PATTERNS_PATH = "/app/config/sanitization_patterns.yaml"
  }

  # Secrets injected as environment variables from Secrets Manager
  # Values retrieved at task launch via task execution role
  # Cached in-memory per application logic (Jira: 1 hour, webhook: 5 min)
  secrets_environment_variables = {
    JIRA_BASE_URL      = "${module.secrets.jira_credentials_secret_arn}:base_url::"
    JIRA_API_TOKEN     = "${module.secrets.jira_credentials_secret_arn}:api_token::"
    JIRA_EMAIL         = "${module.secrets.jira_credentials_secret_arn}:email::"
    VERCEL_WEBHOOK_SECRET = "${module.secrets.webhook_secret_arn}:vercel_secret::"
    GCP_AUDIENCE       = "${module.secrets.webhook_secret_arn}:gcp_audience::"
    MONGODB_URI        = var.enable_mongodb ? "${module.secrets.mongodb_connection_string_secret_arn}::" : ""
    REDIS_AUTH_TOKEN   = "${module.secrets.redis_connection_secret_arn}:auth_token::"
  }

  # CloudWatch Logs configuration for structured JSON log streaming
  # Log group: /aws/ecs/jiratest-error-triage-{env}
  # Retention: 90 days per SOC 2 compliance requirements
  cloudwatch_log_group_name      = "/aws/ecs/jiratest-error-triage-${var.environment}"
  cloudwatch_log_retention_days  = 90
  cloudwatch_log_stream_prefix   = "error-triage"

  # Security group IP whitelisting for webhook sources
  # Vercel webhook IPs: 76.76.21.0/24, 76.76.19.0/24
  # GCP Pub/Sub push IPs: 35.191.0.0/16, 35.187.0.0/16, 108.177.96.0/19
  vercel_webhook_cidr_blocks = [
    "76.76.21.0/24",
    "76.76.19.0/24"
  ]

  gcp_webhook_cidr_blocks = [
    "35.191.0.0/16",
    "35.187.0.0/16",
    "108.177.96.0/19"
  ]

  # Enable Container Insights for infrastructure metrics
  # Publishes CPUUtilization, MemoryUtilization, NetworkRxBytes, NetworkTxBytes
  enable_container_insights = true

  # Prometheus metrics endpoint exposure
  # /metrics endpoint served on container port 8080 for optional scraping
  # CloudWatch remains primary observability integration
  enable_prometheus_metrics = true

  tags = {
    Module = "ecs"
  }

  # Explicit dependencies: IAM roles, secrets, and Redis must exist
  # Redis endpoint required for REDIS_HOST environment variable
  depends_on = [
    module.iam,
    module.secrets,
    module.redis
  ]
}

# -----------------------------------------------------------------------------
# Outputs - Resource Identifiers and Endpoints
# -----------------------------------------------------------------------------
# Aggregates outputs from submodules for root module export
# Used for CI/CD pipeline integration, monitoring dashboard configuration,
# and cross-stack references in dependent infrastructure
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID for jiratest-prod-vpc"
  value       = data.aws_vpc.jiratest_vpc.id
}

output "alb_dns_name" {
  description = "Application Load Balancer DNS name for webhook ingress"
  value       = module.ecs.alb_dns_name
}

output "alb_zone_id" {
  description = "Application Load Balancer Route53 zone ID for DNS alias records"
  value       = module.ecs.alb_zone_id
}

output "ecs_cluster_name" {
  description = "ECS cluster name for service deployment"
  value       = module.ecs.ecs_cluster_name
}

output "ecs_service_name" {
  description = "ECS service name for deployment targeting"
  value       = module.ecs.ecs_service_name
}

output "ecs_task_definition_arn" {
  description = "ECS task definition ARN for deployment history"
  value       = module.ecs.ecs_task_definition_arn
}

output "redis_endpoint" {
  description = "ElastiCache Redis cluster primary endpoint"
  value       = module.redis.redis_endpoint
  sensitive   = false
}

output "redis_port" {
  description = "ElastiCache Redis cluster port (6379)"
  value       = module.redis.redis_port
}

output "cloudwatch_log_group" {
  description = "CloudWatch Logs group name for application logs"
  value       = "/aws/ecs/jiratest-error-triage-${var.environment}"
}

output "jira_credentials_secret_arn" {
  description = "Secrets Manager secret ARN for Jira API credentials"
  value       = module.secrets.jira_credentials_secret_arn
  sensitive   = true
}

output "webhook_secret_arn" {
  description = "Secrets Manager secret ARN for webhook authentication secrets"
  value       = module.secrets.webhook_secret_arn
  sensitive   = true
}

output "mongodb_connection_string_secret_arn" {
  description = "Secrets Manager secret ARN for MongoDB Atlas connection string"
  value       = var.enable_mongodb ? module.secrets.mongodb_connection_string_secret_arn : null
  sensitive   = true
}

output "task_execution_role_arn" {
  description = "ECS task execution role ARN for infrastructure operations"
  value       = module.iam.ecs_task_execution_role_arn
}

output "task_role_arn" {
  description = "ECS task role ARN for application runtime permissions"
  value       = module.iam.ecs_task_role_arn
}

# Service endpoint for external webhook configuration
# Full HTTPS URL: https://error-triage-{env}.jiratest.com/events
output "service_endpoint" {
  description = "HTTPS service endpoint for webhook configuration (Vercel, GCP)"
  value       = "https://${module.ecs.alb_dns_name}/events"
}

output "health_check_endpoint" {
  description = "HTTPS health check endpoint for monitoring"
  value       = "https://${module.ecs.alb_dns_name}/healthz"
}

output "metrics_endpoint" {
  description = "HTTPS Prometheus metrics endpoint for scraping"
  value       = "https://${module.ecs.alb_dns_name}/metrics"
}

# Environment and deployment metadata
output "environment" {
  description = "Deployment environment (dev, staging, production)"
  value       = var.environment
}

output "aws_region" {
  description = "AWS region for resource deployment"
  value       = var.aws_region
}

output "availability_zones" {
  description = "Availability zones for multi-AZ deployment"
  value       = data.aws_availability_zones.available.names
}

# Cost allocation tags for budget tracking
output "resource_tags" {
  description = "Common resource tags for cost allocation and environment identification"
  value = {
    Environment = var.environment
    Service     = "error-triage"
    Project     = "jiratest"
    Team        = "platform-engineering"
    CostCenter  = "infrastructure"
    ManagedBy   = "terraform"
  }
}
