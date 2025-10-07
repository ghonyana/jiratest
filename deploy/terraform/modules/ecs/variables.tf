# ==============================================================================
# ECS Module Variables
# ==============================================================================
# Terraform variable definitions for the ECS Fargate service module.
# This file defines all input parameters required to configure the
# Error Triage → Jira Upserter service infrastructure.
# ==============================================================================

# ------------------------------------------------------------------------------
# Environment Configuration
# ------------------------------------------------------------------------------

variable "environment" {
  description = "Environment identifier for resource naming and configuration (dev, staging, production)"
  type        = string

  validation {
    condition     = can(regex("^(dev|staging|production)$", var.environment))
    error_message = "Environment must be one of: dev, staging, production."
  }
}

variable "project_name" {
  description = "Project name for resource naming and tagging"
  type        = string
  default     = "jiratest-error-triage"
}

# ------------------------------------------------------------------------------
# ECS Task Resource Configuration
# ------------------------------------------------------------------------------

variable "task_cpu" {
  description = <<-EOT
    CPU units for the ECS task (1 vCPU = 1024 units).
    Valid values: "256" (0.25 vCPU), "512" (0.5 vCPU), "1024" (1 vCPU), "2048" (2 vCPU).
    Recommended: "256" for dev, "512" for staging/production.
  EOT
  type        = string
  default     = "512"

  validation {
    condition     = can(regex("^(256|512|1024|2048|4096)$", var.task_cpu))
    error_message = "Task CPU must be a valid Fargate value: 256, 512, 1024, 2048, or 4096."
  }
}

variable "task_memory" {
  description = <<-EOT
    Memory for the ECS task in MiB.
    Must be compatible with task_cpu according to Fargate requirements.
    Valid values: "512", "1024", "2048", "4096", "8192", etc.
    Recommended: "512" for dev, "1024" for staging/production.
  EOT
  type        = string
  default     = "1024"

  validation {
    condition     = can(regex("^(512|1024|2048|3072|4096|5120|6144|7168|8192)$", var.task_memory))
    error_message = "Task memory must be a valid Fargate value between 512 and 8192 MiB."
  }
}

variable "desired_count" {
  description = <<-EOT
    Desired number of ECS tasks to run continuously.
    Recommended: 1 for dev, 2 for staging, 2-4 for production.
  EOT
  type        = number
  default     = 2

  validation {
    condition     = var.desired_count >= 1 && var.desired_count <= 20
    error_message = "Desired count must be between 1 and 20."
  }
}

# ------------------------------------------------------------------------------
# Autoscaling Configuration
# ------------------------------------------------------------------------------

variable "min_capacity" {
  description = <<-EOT
    Minimum number of ECS tasks for autoscaling.
    Recommended: 1 for dev, 2 for staging/production (for high availability).
  EOT
  type        = number
  default     = 2

  validation {
    condition     = var.min_capacity >= 1 && var.min_capacity <= 20
    error_message = "Minimum capacity must be between 1 and 20."
  }
}

variable "max_capacity" {
  description = <<-EOT
    Maximum number of ECS tasks for autoscaling.
    Recommended: 4 for dev, 10 for staging, 20 for production.
  EOT
  type        = number
  default     = 10

  validation {
    condition     = var.max_capacity >= 1 && var.max_capacity <= 100
    error_message = "Maximum capacity must be between 1 and 100."
  }
}

variable "target_cpu_utilization" {
  description = <<-EOT
    Target CPU utilization percentage for autoscaling trigger.
    Recommended: 70% for production workloads.
  EOT
  type        = number
  default     = 70

  validation {
    condition     = var.target_cpu_utilization >= 10 && var.target_cpu_utilization <= 90
    error_message = "Target CPU utilization must be between 10 and 90 percent."
  }
}

variable "scale_in_cooldown" {
  description = "Cooldown period in seconds before allowing another scale-in activity"
  type        = number
  default     = 300
}

variable "scale_out_cooldown" {
  description = "Cooldown period in seconds before allowing another scale-out activity"
  type        = number
  default     = 60
}

# ------------------------------------------------------------------------------
# Networking Configuration
# ------------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC ID where ECS service and ALB will be deployed"
  type        = string

  validation {
    condition     = can(regex("^vpc-[a-z0-9]+$", var.vpc_id))
    error_message = "VPC ID must be a valid AWS VPC identifier (vpc-*)."
  }
}

variable "public_subnet_ids" {
  description = <<-EOT
    List of public subnet IDs for Application Load Balancer deployment.
    Must be in at least 2 different availability zones for high availability.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "At least 2 public subnets are required for ALB high availability."
  }
}

variable "private_subnet_ids" {
  description = <<-EOT
    List of private subnet IDs for ECS task deployment.
    Tasks run in private subnets for enhanced security.
    Must be in at least 2 different availability zones for high availability.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "At least 2 private subnets are required for ECS task high availability."
  }
}

variable "alb_security_group_id" {
  description = "Security group ID for the Application Load Balancer (allows inbound HTTPS from Vercel/GCP)"
  type        = string

  validation {
    condition     = can(regex("^sg-[a-z0-9]+$", var.alb_security_group_id))
    error_message = "ALB security group ID must be a valid AWS security group identifier (sg-*)."
  }
}

variable "ecs_security_group_id" {
  description = "Security group ID for ECS tasks (allows inbound from ALB, outbound to Jira/Redis/MongoDB)"
  type        = string

  validation {
    condition     = can(regex("^sg-[a-z0-9]+$", var.ecs_security_group_id))
    error_message = "ECS security group ID must be a valid AWS security group identifier (sg-*)."
  }
}

# ------------------------------------------------------------------------------
# Container Configuration
# ------------------------------------------------------------------------------

variable "ecr_repository_url" {
  description = <<-EOT
    Full URL of the ECR repository containing the error-triage container image.
    Format: {account_id}.dkr.ecr.{region}.amazonaws.com/{repository_name}
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]+\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/.+$", var.ecr_repository_url))
    error_message = "ECR repository URL must be a valid AWS ECR URL."
  }
}

variable "container_image_tag" {
  description = <<-EOT
    Container image tag to deploy.
    Recommended: Use Git SHA for traceability (e.g., "abc123def").
    Default: "latest" for development environments.
  EOT
  type        = string
  default     = "latest"
}

variable "container_port" {
  description = "Port on which the container application listens (Gunicorn WSGI server)"
  type        = number
  default     = 8080

  validation {
    condition     = var.container_port >= 1024 && var.container_port <= 65535
    error_message = "Container port must be between 1024 and 65535."
  }
}

variable "container_name" {
  description = "Name of the container within the ECS task definition"
  type        = string
  default     = "error-triage"
}

# ------------------------------------------------------------------------------
# AWS Secrets Manager Integration
# ------------------------------------------------------------------------------

variable "jira_credentials_secret_arn" {
  description = <<-EOT
    ARN of the Secrets Manager secret containing Jira API credentials.
    Expected JSON structure: {"base_url": "https://org.atlassian.net", "api_token": "..."}
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]+:secret:.+$", var.jira_credentials_secret_arn))
    error_message = "Jira credentials secret ARN must be a valid AWS Secrets Manager ARN."
  }
}

variable "webhook_secret_arn" {
  description = <<-EOT
    ARN of the Secrets Manager secret containing webhook signature verification secrets.
    Expected JSON structure: {"vercel_secret": "...", "gcp_audience": "https://..."}
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]+:secret:.+$", var.webhook_secret_arn))
    error_message = "Webhook secret ARN must be a valid AWS Secrets Manager ARN."
  }
}

variable "mongodb_connection_string_secret_arn" {
  description = <<-EOT
    ARN of the Secrets Manager secret containing MongoDB Atlas connection string.
    Expected JSON structure: {"connection_string": "mongodb+srv://..."}
    Optional: Only required when ENABLE_MONGO=true.
  EOT
  type        = string
  default     = ""
}

# ------------------------------------------------------------------------------
# Redis Configuration
# ------------------------------------------------------------------------------

variable "redis_endpoint" {
  description = <<-EOT
    Redis cluster endpoint for frequency counters and event deduplication.
    Format: {cluster-id}.{random}.{region}.cache.amazonaws.com:6379
    Used to set REDIS_HOST environment variable in the container.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9.-]+:\\d+$", var.redis_endpoint)) || var.redis_endpoint == ""
    error_message = "Redis endpoint must be in format 'hostname:port'."
  }
}

# ------------------------------------------------------------------------------
# CloudWatch Logs Configuration
# ------------------------------------------------------------------------------

variable "log_group_name" {
  description = <<-EOT
    CloudWatch Logs group name for ECS task logs.
    Format: /aws/ecs/{project_name}-{environment}
  EOT
  type        = string

  validation {
    condition     = can(regex("^/aws/ecs/.+$", var.log_group_name))
    error_message = "Log group name must follow CloudWatch Logs naming convention (/aws/ecs/...)."
  }
}

variable "log_retention_days" {
  description = <<-EOT
    Number of days to retain logs in CloudWatch Logs.
    Recommended: 7 for dev, 30 for staging, 90 for production.
  EOT
  type        = number
  default     = 90

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.log_retention_days)
    error_message = "Log retention days must be a valid CloudWatch Logs retention period."
  }
}

variable "log_stream_prefix" {
  description = "Prefix for CloudWatch Logs stream names"
  type        = string
  default     = "ecs"
}

# ------------------------------------------------------------------------------
# Health Check Configuration
# ------------------------------------------------------------------------------

variable "health_check_grace_period_seconds" {
  description = <<-EOT
    Grace period in seconds before ECS starts health checks after task startup.
    Allows time for application initialization (dependencies, configuration loading).
    Recommended: 60 seconds for typical startup time.
  EOT
  type        = number
  default     = 60

  validation {
    condition     = var.health_check_grace_period_seconds >= 0 && var.health_check_grace_period_seconds <= 300
    error_message = "Health check grace period must be between 0 and 300 seconds."
  }
}

variable "health_check_path" {
  description = "HTTP path for ALB health check endpoint"
  type        = string
  default     = "/healthz"

  validation {
    condition     = can(regex("^/.*$", var.health_check_path))
    error_message = "Health check path must start with '/'."
  }
}

variable "health_check_interval" {
  description = "Interval in seconds between health checks"
  type        = number
  default     = 30

  validation {
    condition     = var.health_check_interval >= 5 && var.health_check_interval <= 300
    error_message = "Health check interval must be between 5 and 300 seconds."
  }
}

variable "health_check_timeout" {
  description = "Timeout in seconds for health check requests"
  type        = number
  default     = 5

  validation {
    condition     = var.health_check_timeout >= 2 && var.health_check_timeout <= 120
    error_message = "Health check timeout must be between 2 and 120 seconds."
  }
}

variable "health_check_healthy_threshold" {
  description = "Number of consecutive successful health checks required to mark target as healthy"
  type        = number
  default     = 2

  validation {
    condition     = var.health_check_healthy_threshold >= 2 && var.health_check_healthy_threshold <= 10
    error_message = "Healthy threshold must be between 2 and 10."
  }
}

variable "health_check_unhealthy_threshold" {
  description = "Number of consecutive failed health checks required to mark target as unhealthy"
  type        = number
  default     = 3

  validation {
    condition     = var.health_check_unhealthy_threshold >= 2 && var.health_check_unhealthy_threshold <= 10
    error_message = "Unhealthy threshold must be between 2 and 10."
  }
}

variable "health_check_matcher" {
  description = "HTTP status codes that indicate a successful health check"
  type        = string
  default     = "200"
}

# ------------------------------------------------------------------------------
# Deployment Configuration
# ------------------------------------------------------------------------------

variable "deployment_maximum_percent" {
  description = <<-EOT
    Maximum percentage of tasks that can run during a deployment.
    200 allows rolling updates: new tasks start before old tasks stop.
  EOT
  type        = number
  default     = 200

  validation {
    condition     = var.deployment_maximum_percent >= 100 && var.deployment_maximum_percent <= 200
    error_message = "Deployment maximum percent must be between 100 and 200."
  }
}

variable "deployment_minimum_healthy_percent" {
  description = <<-EOT
    Minimum percentage of tasks that must remain running during a deployment.
    50 allows half the tasks to stop during updates for faster deployments.
  EOT
  type        = number
  default     = 50

  validation {
    condition     = var.deployment_minimum_healthy_percent >= 0 && var.deployment_minimum_healthy_percent <= 100
    error_message = "Deployment minimum healthy percent must be between 0 and 100."
  }
}

variable "enable_circuit_breaker" {
  description = "Enable ECS deployment circuit breaker to automatically roll back failed deployments"
  type        = bool
  default     = true
}

variable "circuit_breaker_rollback" {
  description = "Automatically roll back failed deployments when circuit breaker is enabled"
  type        = bool
  default     = true
}

# ------------------------------------------------------------------------------
# IAM Roles
# ------------------------------------------------------------------------------

variable "task_execution_role_arn" {
  description = <<-EOT
    ARN of the IAM role for ECS task execution (infrastructure operations).
    Permissions required: ECR image pull, CloudWatch Logs write, Secrets Manager read.
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]+:role/.+$", var.task_execution_role_arn))
    error_message = "Task execution role ARN must be a valid AWS IAM role ARN."
  }
}

variable "task_role_arn" {
  description = <<-EOT
    ARN of the IAM role for application runtime permissions.
    Permissions required: Secrets Manager read (for credentials), CloudWatch metrics write.
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]+:role/.+$", var.task_role_arn))
    error_message = "Task role ARN must be a valid AWS IAM role ARN."
  }
}

# ------------------------------------------------------------------------------
# Application Environment Variables
# ------------------------------------------------------------------------------

variable "enable_mongodb" {
  description = "Enable MongoDB audit logging (requires mongodb_connection_string_secret_arn)"
  type        = bool
  default     = false
}

variable "flask_env" {
  description = "Flask application environment (development, production)"
  type        = string
  default     = "production"

  validation {
    condition     = contains(["development", "production"], var.flask_env)
    error_message = "Flask environment must be 'development' or 'production'."
  }
}

variable "log_level" {
  description = "Application log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], var.log_level)
    error_message = "Log level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL."
  }
}

variable "jira_project_key" {
  description = "Jira project key for creating error issues (e.g., 'ET' for Error Triage)"
  type        = string
  default     = "ET"

  validation {
    condition     = can(regex("^[A-Z]{2,10}$", var.jira_project_key))
    error_message = "Jira project key must be 2-10 uppercase letters."
  }
}

variable "comment_rate_limit_minutes" {
  description = "Minimum minutes between comments on the same Jira issue (unless severity escalates)"
  type        = number
  default     = 15

  validation {
    condition     = var.comment_rate_limit_minutes >= 1 && var.comment_rate_limit_minutes <= 60
    error_message = "Comment rate limit must be between 1 and 60 minutes."
  }
}

variable "event_deduplication_ttl_seconds" {
  description = "TTL for event deduplication cache in Redis (prevents duplicate processing)"
  type        = number
  default     = 3600

  validation {
    condition     = var.event_deduplication_ttl_seconds >= 300 && var.event_deduplication_ttl_seconds <= 7200
    error_message = "Event deduplication TTL must be between 300 and 7200 seconds."
  }
}

variable "frequency_counter_ttl_seconds" {
  description = "TTL for frequency counters in Redis (rolling window for severity calculation)"
  type        = number
  default     = 300

  validation {
    condition     = var.frequency_counter_ttl_seconds >= 60 && var.frequency_counter_ttl_seconds <= 600
    error_message = "Frequency counter TTL must be between 60 and 600 seconds."
  }
}

# ------------------------------------------------------------------------------
# Tagging
# ------------------------------------------------------------------------------

variable "tags" {
  description = "Additional tags to apply to all resources created by this module"
  type        = map(string)
  default     = {}
}
