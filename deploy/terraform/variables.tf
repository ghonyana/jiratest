# =============================================================================
# Terraform Variables for Error Triage → Jira Upserter Service Infrastructure
# =============================================================================
# This file defines all input parameters required for provisioning the
# Error Triage → Jira Upserter service infrastructure across multiple
# environments (development, staging, production).
#
# Variable values are provided through environment-specific .tfvars files:
#   - dev.tfvars
#   - staging.tfvars
#   - production.tfvars
# =============================================================================

# -----------------------------------------------------------------------------
# Environment Configuration
# -----------------------------------------------------------------------------

variable "environment" {
  description = "Deployment environment identifier. Determines resource sizing, multi-AZ configuration, and naming conventions."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: dev, staging, production."
  }
}

variable "project_name" {
  description = "Project identifier used in resource naming and tagging. Follows naming convention: jiratest-{environment}-{resource-type}-{identifier}"
  type        = string
  default     = "jiratest-error-triage"
}

# -----------------------------------------------------------------------------
# AWS Region and Availability Zone Configuration
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for infrastructure deployment. us-east-1 selected for largest service availability, lowest latency for US users, and organizational VPC integration."
  type        = string
  default     = "us-east-1"
}

variable "availability_zones" {
  description = "Availability zones for multi-AZ deployment. Distributes Application Load Balancer, ECS tasks, and ElastiCache Redis across zones for high availability."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

# -----------------------------------------------------------------------------
# ECS Fargate Task Configuration
# -----------------------------------------------------------------------------

variable "ecs_task_cpu" {
  description = "ECS Fargate task vCPU allocation in CPU units (1024 = 1 vCPU). Production/Staging: 512 units (0.5 vCPU) supports ~20 req/s per task. Development: 256 units (0.25 vCPU) for cost optimization."
  type        = number

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.ecs_task_cpu)
    error_message = "ECS task CPU must be valid Fargate value: 256, 512, 1024, 2048, or 4096."
  }
}

variable "ecs_task_memory" {
  description = "ECS Fargate task memory allocation in MiB. Must be compatible with CPU allocation per Fargate requirements. Production/Staging: 1024 MB (Python runtime 250 MB + Redis pool 50 MB + buffers 200 MB + safety margin). Development: 512 MB."
  type        = number

  validation {
    condition     = var.ecs_task_memory >= 512 && var.ecs_task_memory <= 30720
    error_message = "ECS task memory must be between 512 MiB and 30720 MiB and compatible with CPU allocation."
  }
}

variable "ecs_task_desired_count" {
  description = "Desired number of ECS tasks to run continuously. Production: 6 tasks for baseline 100 req/s capacity with multi-AZ distribution. Staging: 4 tasks for production-like testing. Development: 2 tasks for minimal cost."
  type        = number
  default     = 2

  validation {
    condition     = var.ecs_task_desired_count >= 1 && var.ecs_task_desired_count <= 50
    error_message = "Desired task count must be between 1 and 50."
  }
}

variable "ecs_autoscaling_min_capacity" {
  description = "Minimum number of ECS tasks maintained during autoscaling scale-down events. Ensures baseline availability even during low traffic periods."
  type        = number
  default     = 2

  validation {
    condition     = var.ecs_autoscaling_min_capacity >= 1 && var.ecs_autoscaling_min_capacity <= 10
    error_message = "Autoscaling minimum capacity must be between 1 and 10."
  }
}

variable "ecs_autoscaling_max_capacity" {
  description = "Maximum number of ECS tasks during autoscaling scale-up events. Production: 20 tasks supports 400 req/s peak capacity (20 rps/task × 20 tasks). Staging/Development: Lower limits for cost control."
  type        = number
  default     = 20

  validation {
    condition     = var.ecs_autoscaling_max_capacity >= 2 && var.ecs_autoscaling_max_capacity <= 50
    error_message = "Autoscaling maximum capacity must be between 2 and 50."
  }
}

variable "ecs_autoscaling_target_cpu" {
  description = "Target CPU utilization percentage for ECS autoscaling. When average CPU exceeds this threshold, ECS launches additional tasks. 70% provides capacity headroom while preventing excessive scaling."
  type        = number
  default     = 70

  validation {
    condition     = var.ecs_autoscaling_target_cpu >= 50 && var.ecs_autoscaling_target_cpu <= 90
    error_message = "Autoscaling target CPU must be between 50% and 90%."
  }
}

variable "ecs_health_check_grace_period" {
  description = "Grace period in seconds before ECS health checks start evaluating task health. Allows application initialization, secret retrieval, and dependency connections to complete."
  type        = number
  default     = 60

  validation {
    condition     = var.ecs_health_check_grace_period >= 30 && var.ecs_health_check_grace_period <= 300
    error_message = "Health check grace period must be between 30 and 300 seconds."
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Redis Cluster Configuration
# -----------------------------------------------------------------------------

variable "redis_node_type" {
  description = "ElastiCache Redis node instance type. Production: cache.t4g.medium (1.2 GB, ~20,000 ops/sec). Staging: cache.t4g.small (0.5 GB). Development: cache.t4g.micro (0.5 GB). t4g family uses AWS Graviton2 for 20% cost savings."
  type        = string

  validation {
    condition     = can(regex("^cache\\.(t4g|t3|r6g|r5)\\.(micro|small|medium|large|xlarge)$", var.redis_node_type))
    error_message = "Redis node type must be valid ElastiCache instance type (e.g., cache.t4g.medium)."
  }
}

variable "redis_num_cache_clusters" {
  description = "Number of Redis cache nodes in cluster. Production: 2 nodes (primary + replica) for multi-AZ automatic failover. Staging/Development: 1 node for cost optimization."
  type        = number
  default     = 1

  validation {
    condition     = var.redis_num_cache_clusters >= 1 && var.redis_num_cache_clusters <= 6
    error_message = "Redis cache cluster count must be between 1 and 6."
  }
}

variable "redis_engine_version" {
  description = "ElastiCache Redis engine version. 7.2+ required for enhanced ACL support, improved atomic operations (INCR, SETEX), and TLS in-transit encryption."
  type        = string
  default     = "7.2"

  validation {
    condition     = can(regex("^(7\\.[2-9]|[8-9]\\.[0-9])$", var.redis_engine_version))
    error_message = "Redis engine version must be 7.2 or higher."
  }
}

variable "redis_parameter_group_family" {
  description = "Redis parameter group family corresponding to engine version. Used for cluster configuration tuning (AOF persistence, fsync frequency, timeout settings)."
  type        = string
  default     = "redis7"
}

variable "redis_port" {
  description = "TCP port for Redis cluster connections. Standard Redis port used for application connections from ECS tasks."
  type        = number
  default     = 6379

  validation {
    condition     = var.redis_port >= 1024 && var.redis_port <= 65535
    error_message = "Redis port must be between 1024 and 65535."
  }
}

variable "redis_snapshot_retention_limit" {
  description = "Number of days to retain automatic Redis RDB snapshots. Production: 7 days for disaster recovery. Development: 1 day for cost optimization."
  type        = number
  default     = 1

  validation {
    condition     = var.redis_snapshot_retention_limit >= 0 && var.redis_snapshot_retention_limit <= 35
    error_message = "Redis snapshot retention must be between 0 and 35 days."
  }
}

variable "redis_snapshot_window" {
  description = "Daily time range (UTC) for automated Redis RDB snapshots. Example: '03:00-05:00' for 3-5 AM UTC maintenance window."
  type        = string
  default     = "03:00-05:00"

  validation {
    condition     = can(regex("^([0-1][0-9]|2[0-3]):[0-5][0-9]-([0-1][0-9]|2[0-3]):[0-5][0-9]$", var.redis_snapshot_window))
    error_message = "Redis snapshot window must be in format 'HH:MM-HH:MM' (UTC)."
  }
}

variable "redis_maintenance_window" {
  description = "Weekly time range (UTC) for Redis cluster maintenance operations (engine upgrades, security patches). Example: 'sun:05:00-sun:07:00'."
  type        = string
  default     = "sun:05:00-sun:07:00"

  validation {
    condition     = can(regex("^(mon|tue|wed|thu|fri|sat|sun):[0-2][0-9]:[0-5][0-9]-(mon|tue|wed|thu|fri|sat|sun):[0-2][0-9]:[0-5][0-9]$", var.redis_maintenance_window))
    error_message = "Redis maintenance window must be in format 'ddd:HH:MM-ddd:HH:MM' (UTC)."
  }
}

variable "redis_automatic_failover_enabled" {
  description = "Enable automatic failover for multi-AZ Redis clusters. Production: true for 60-90 second failover to replica. Staging/Development: false (single-node clusters)."
  type        = bool
  default     = false
}

variable "redis_at_rest_encryption_enabled" {
  description = "Enable encryption at rest for Redis snapshots. Recommended for production compliance requirements, optional for ephemeral cache data."
  type        = bool
  default     = false
}

variable "redis_transit_encryption_enabled" {
  description = "Enable TLS in-transit encryption for Redis connections. Recommended for production to encrypt frequency counters and rate limit data, adds ~5ms latency overhead."
  type        = bool
  default     = false
}

variable "redis_auth_token_enabled" {
  description = "Enable Redis AUTH token authentication. Provides additional authentication layer beyond security group network isolation."
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# VPC and Networking Configuration
# -----------------------------------------------------------------------------

variable "vpc_id" {
  description = "ID of existing VPC for resource deployment. Reuses organizational jiratest-prod-vpc (10.0.0.0/16 CIDR) for network isolation and integration with existing infrastructure."
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for Application Load Balancer deployment across availability zones. ALB receives inbound HTTPS webhook traffic from Vercel and GCP."
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "At least 2 public subnets required for ALB multi-AZ deployment."
  }
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for ECS Fargate task deployment across availability zones. Tasks have no direct internet access; route through NAT Gateway for outbound connections."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "At least 2 private subnets required for ECS task multi-AZ deployment."
  }
}

variable "data_subnet_ids" {
  description = "List of private data subnet IDs for ElastiCache Redis cluster deployment. Isolated data-tier subnets accessible only from ECS task security group."
  type        = list(string)

  validation {
    condition     = length(var.data_subnet_ids) >= 1
    error_message = "At least 1 data subnet required for ElastiCache deployment."
  }
}

variable "vpc_cidr_block" {
  description = "CIDR block of existing VPC for security group rule configuration. Used for internal health check source rules and VPC-internal traffic allowlisting."
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "VPC CIDR block must be valid CIDR notation."
  }
}

# -----------------------------------------------------------------------------
# Application Load Balancer Configuration
# -----------------------------------------------------------------------------

variable "alb_internal" {
  description = "Deploy Application Load Balancer as internal (VPC-only) or internet-facing. false = internet-facing for webhook ingress from Vercel and GCP external platforms."
  type        = bool
  default     = false
}

variable "alb_enable_deletion_protection" {
  description = "Prevent accidental Application Load Balancer deletion through AWS console or API. Production: true for safety. Development: false for easy teardown."
  type        = bool
  default     = false
}

variable "alb_enable_cross_zone_load_balancing" {
  description = "Distribute traffic evenly across targets in all enabled availability zones. Recommended true for consistent webhook processing latency."
  type        = bool
  default     = true
}

variable "alb_idle_timeout" {
  description = "Idle timeout in seconds before ALB closes idle connections. 60 seconds sufficient for webhook POST requests with typical 200ms processing time."
  type        = number
  default     = 60

  validation {
    condition     = var.alb_idle_timeout >= 1 && var.alb_idle_timeout <= 3600
    error_message = "ALB idle timeout must be between 1 and 3600 seconds."
  }
}

variable "alb_health_check_path" {
  description = "HTTP path for Application Load Balancer target group health checks. /healthz endpoint validates dependency connectivity (Redis, MongoDB, Jira)."
  type        = string
  default     = "/healthz"
}

variable "alb_health_check_interval" {
  description = "Health check probe interval in seconds. 30 seconds balances fast failure detection with health check overhead on application resources."
  type        = number
  default     = 30

  validation {
    condition     = var.alb_health_check_interval >= 5 && var.alb_health_check_interval <= 300
    error_message = "Health check interval must be between 5 and 300 seconds."
  }
}

variable "alb_health_check_timeout" {
  description = "Health check probe timeout in seconds. 5 seconds sufficient for /healthz endpoint with typical < 50ms response time for healthy dependencies."
  type        = number
  default     = 5

  validation {
    condition     = var.alb_health_check_timeout >= 2 && var.alb_health_check_timeout <= 120
    error_message = "Health check timeout must be between 2 and 120 seconds."
  }
}

variable "alb_health_check_healthy_threshold" {
  description = "Number of consecutive successful health checks before marking target healthy. 2 checks = 60 second recovery time for tasks with temporary dependency failures."
  type        = number
  default     = 2

  validation {
    condition     = var.alb_health_check_healthy_threshold >= 2 && var.alb_health_check_healthy_threshold <= 10
    error_message = "Healthy threshold must be between 2 and 10 consecutive checks."
  }
}

variable "alb_health_check_unhealthy_threshold" {
  description = "Number of consecutive failed health checks before marking target unhealthy. 2 checks = 60 second detection time for sustained dependency issues (Redis connection loss, Jira unavailability)."
  type        = number
  default     = 2

  validation {
    condition     = var.alb_health_check_unhealthy_threshold >= 2 && var.alb_health_check_unhealthy_threshold <= 10
    error_message = "Unhealthy threshold must be between 2 and 10 consecutive checks."
  }
}

variable "alb_health_check_matcher" {
  description = "HTTP status codes considered successful for health checks. '200' = task healthy with all dependencies UP. '503' = task unhealthy, ALB removes from rotation."
  type        = string
  default     = "200"
}

variable "alb_deregistration_delay" {
  description = "Connection draining timeout in seconds before deregistering target. 30 seconds allows in-flight webhook requests to complete during task shutdown or deployment."
  type        = number
  default     = 30

  validation {
    condition     = var.alb_deregistration_delay >= 0 && var.alb_deregistration_delay <= 3600
    error_message = "Deregistration delay must be between 0 and 3600 seconds."
  }
}

variable "acm_certificate_arn" {
  description = "ARN of AWS Certificate Manager certificate for ALB HTTPS listener. Wildcard certificate (*.jiratest.com) with automatic renewal and zero-downtime updates."
  type        = string
  default     = ""
}

variable "alb_security_policy" {
  description = "ALB TLS security policy. ELBSecurityPolicy-TLS-1-2-2017-01 enforces TLS 1.2 minimum, supports TLS 1.3, uses FIPS-compliant cipher suites."
  type        = string
  default     = "ELBSecurityPolicy-TLS-1-2-2017-01"
}

# -----------------------------------------------------------------------------
# Webhook Source IP Allowlisting
# -----------------------------------------------------------------------------

variable "vercel_webhook_ip_ranges" {
  description = "CIDR blocks for Vercel Log Drain webhook source IPs. ALB security group restricts inbound HTTPS to these ranges for webhook ingress."
  type        = list(string)
  default     = ["76.76.21.0/24", "76.76.19.0/24"]
}

variable "gcp_webhook_ip_ranges" {
  description = "CIDR blocks for GCP Cloud Logging Pub/Sub push webhook source IPs. ALB security group restricts inbound HTTPS to these ranges for webhook ingress."
  type        = list(string)
  default     = ["35.191.0.0/16", "35.187.0.0/16", "108.177.96.0/19"]
}

# -----------------------------------------------------------------------------
# AWS Secrets Manager Configuration
# -----------------------------------------------------------------------------

variable "secrets_manager_jira_secret_name" {
  description = "Name of Secrets Manager secret storing Jira API credentials. Pattern: jira/jiratest/{env}/credentials with JSON: {base_url, email, api_token}."
  type        = string
  default     = ""
}

variable "secrets_manager_webhook_secret_name" {
  description = "Name of Secrets Manager secret storing webhook authentication secrets. Pattern: jira/jiratest/{env}/webhook-secret with JSON: {vercel_secret, gcp_audience}."
  type        = string
  default     = ""
}

variable "secrets_manager_mongodb_secret_name" {
  description = "Name of Secrets Manager secret storing MongoDB Atlas connection string. Pattern: mongodb/jiratest/{env}/connection-string."
  type        = string
  default     = ""
}

variable "secrets_manager_kms_key_id" {
  description = "KMS key ID for Secrets Manager encryption. Uses customer managed key with automatic annual rotation. Empty string = AWS managed key."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# CloudWatch Logs Configuration
# -----------------------------------------------------------------------------

variable "cloudwatch_log_group_name" {
  description = "CloudWatch Logs group name for ECS task logs. Pattern: /aws/ecs/jiratest-error-triage-{env} for environment-specific log isolation and structured JSON log streaming."
  type        = string
  default     = ""
}

variable "cloudwatch_log_retention_days" {
  description = "CloudWatch Logs retention period in days. 90 days balances operational troubleshooting window with SOC 2 compliance requirements and storage cost optimization."
  type        = number
  default     = 90

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.cloudwatch_log_retention_days)
    error_message = "CloudWatch log retention must be valid value: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, or 3653 days."
  }
}

variable "cloudwatch_log_stream_prefix" {
  description = "Prefix for CloudWatch log stream names. Pattern: error-triage/{task_id} for task-level log isolation and debugging."
  type        = string
  default     = "error-triage"
}

# -----------------------------------------------------------------------------
# Amazon ECR Configuration
# -----------------------------------------------------------------------------

variable "ecr_repository_name" {
  description = "ECR repository name for Docker image storage. Centralized repository for all environment images with multi-tag strategy (git SHA, semantic version, environment-timestamp)."
  type        = string
  default     = "jiratest-error-triage"
}

variable "ecr_image_tag_mutability" {
  description = "ECR image tag mutability. MUTABLE allows 'latest' tag updates for convenience. IMMUTABLE prevents tag overwrites for production traceability."
  type        = string
  default     = "MUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.ecr_image_tag_mutability)
    error_message = "ECR image tag mutability must be MUTABLE or IMMUTABLE."
  }
}

variable "ecr_scan_on_push" {
  description = "Enable automatic vulnerability scanning on Docker image push. Detects CVEs in base images and Python dependencies, blocks deployment of images with critical vulnerabilities (CVSS ≥ 7.0)."
  type        = bool
  default     = true
}

variable "ecr_lifecycle_policy_count" {
  description = "Number of tagged images to retain in ECR repository. 10 images balances rollback availability (last 10 deployments) with storage cost optimization."
  type        = number
  default     = 10

  validation {
    condition     = var.ecr_lifecycle_policy_count >= 1 && var.ecr_lifecycle_policy_count <= 100
    error_message = "ECR lifecycle policy count must be between 1 and 100."
  }
}

variable "ecr_untagged_image_retention_days" {
  description = "Retention period in days for untagged ECR images. 7 days allows build artifact inspection before automatic cleanup."
  type        = number
  default     = 7

  validation {
    condition     = var.ecr_untagged_image_retention_days >= 1 && var.ecr_untagged_image_retention_days <= 365
    error_message = "ECR untagged image retention must be between 1 and 365 days."
  }
}

# -----------------------------------------------------------------------------
# Resource Tagging Strategy
# -----------------------------------------------------------------------------

variable "tags" {
  description = "Common resource tags for cost allocation, environment identification, and organizational tracking. Applied to all AWS resources (ECS, ALB, Redis, ECR, Secrets Manager, CloudWatch)."
  type        = map(string)
  default     = {}
}

variable "additional_tags" {
  description = "Additional custom tags for specific resource requirements or organizational policies. Merged with common tags."
  type        = map(string)
  default     = {}
}

# Standard tags computed from variables
locals {
  common_tags = merge(
    {
      Environment = var.environment
      Service     = var.project_name
      ManagedBy   = "Terraform"
      Project     = "jiratest-error-triage"
    },
    var.tags,
    var.additional_tags
  )
}

# -----------------------------------------------------------------------------
# Container Configuration
# -----------------------------------------------------------------------------

variable "container_name" {
  description = "Name of container in ECS task definition. Used for ALB target group integration and CloudWatch log stream identification."
  type        = string
  default     = "error-triage-app"
}

variable "container_port" {
  description = "TCP port exposed by Flask application container. Gunicorn WSGI server listens on port 8000 for HTTP traffic after ALB TLS termination."
  type        = number
  default     = 8000

  validation {
    condition     = var.container_port >= 1024 && var.container_port <= 65535
    error_message = "Container port must be between 1024 and 65535."
  }
}

# -----------------------------------------------------------------------------
# MongoDB Atlas Configuration (Optional)
# -----------------------------------------------------------------------------

variable "enable_mongodb" {
  description = "Enable MongoDB Atlas integration for audit logging, Jira action tracking, and configuration versioning. Optional for v1 deployment."
  type        = bool
  default     = false
}

variable "mongodb_connection_string_secret_arn" {
  description = "ARN of Secrets Manager secret containing MongoDB Atlas connection string. Required when enable_mongodb = true."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Cost Optimization Configuration
# -----------------------------------------------------------------------------

variable "enable_cost_optimization" {
  description = "Enable cost optimization features: aggressive autoscaling schedules for non-production, S3 log archival, CloudWatch Logs export. Development/Staging recommended."
  type        = bool
  default     = false
}

variable "autoscaling_schedule_scale_down_cron" {
  description = "Cron expression for autoscaling scale-down schedule in non-production environments. Example: 'cron(0 22 * * ? *)' for 10 PM UTC daily scale-down."
  type        = string
  default     = ""
}

variable "autoscaling_schedule_scale_up_cron" {
  description = "Cron expression for autoscaling scale-up schedule in non-production environments. Example: 'cron(0 6 * * ? *)' for 6 AM UTC daily scale-up."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Monitoring and Alerting Configuration
# -----------------------------------------------------------------------------

variable "enable_container_insights" {
  description = "Enable ECS Container Insights for enhanced infrastructure metrics (CPUUtilization, MemoryUtilization, NetworkRxBytes). Adds $0.30 per task per month cost."
  type        = bool
  default     = true
}

variable "cloudwatch_alarm_email_endpoints" {
  description = "List of email addresses for CloudWatch alarm notifications. Receives alerts for high error rates, authentication failures, processing latency, unhealthy tasks."
  type        = list(string)
  default     = []
}

variable "enable_prometheus_metrics" {
  description = "Expose /metrics endpoint for Prometheus-format metrics. Primary metrics source for events_received_total, jira_issues_created_total, event_processing_duration_seconds."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Deployment Configuration
# -----------------------------------------------------------------------------

variable "deployment_maximum_percent" {
  description = "Maximum percentage of desired tasks that can run during ECS deployment. 200 = rolling update launches new tasks before terminating old tasks for zero-downtime deployments."
  type        = number
  default     = 200

  validation {
    condition     = var.deployment_maximum_percent >= 100 && var.deployment_maximum_percent <= 200
    error_message = "Deployment maximum percent must be between 100 and 200."
  }
}

variable "deployment_minimum_healthy_percent" {
  description = "Minimum percentage of desired tasks that must remain healthy during ECS deployment. 50 = rolling update replaces 50% of tasks at a time, maintains half capacity during rollouts."
  type        = number
  default     = 50

  validation {
    condition     = var.deployment_minimum_healthy_percent >= 0 && var.deployment_minimum_healthy_percent <= 100
    error_message = "Deployment minimum healthy percent must be between 0 and 100."
  }
}

variable "enable_execute_command" {
  description = "Enable ECS Exec for task debugging via AWS CLI. Provides SSH-like access to running containers for troubleshooting. Security trade-off: audit all command execution."
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Terraform State Management
# -----------------------------------------------------------------------------

variable "terraform_state_bucket" {
  description = "S3 bucket name for Terraform remote state storage. Pattern: jiratest-terraform-state with versioning enabled for rollback capability."
  type        = string
  default     = "jiratest-terraform-state"
}

variable "terraform_state_lock_table" {
  description = "DynamoDB table name for Terraform state locking. Pattern: jiratest-terraform-locks prevents concurrent modifications and state corruption."
  type        = string
  default     = "jiratest-terraform-locks"
}

variable "terraform_state_key_prefix" {
  description = "S3 key prefix for environment-specific Terraform state files. Pattern: error-triage/{env}/terraform.tfstate for state isolation per deployment stage."
  type        = string
  default     = "error-triage"
}
