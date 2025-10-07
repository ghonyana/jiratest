# =============================================================================
# Terraform Root Module Outputs
# Error Triage → Jira Upserter Infrastructure
# =============================================================================
# 
# This file defines comprehensive output values for the Error Triage service
# infrastructure, exposing resource identifiers, endpoints, and configuration
# values consumed by:
#   - CI/CD pipelines (GitHub Actions)
#   - Monitoring dashboards (CloudWatch, Grafana)
#   - Operational runbooks and troubleshooting procedures
#   - External service integration (MongoDB Atlas IP whitelisting)
#
# Output naming conventions follow HashiCorp best practices:
#   - snake_case naming
#   - Descriptive names indicating resource type and purpose
#   - Sensitive values marked with sensitive = true
# =============================================================================

# -----------------------------------------------------------------------------
# ECS Service Outputs
# Purpose: Expose ECS service metadata for deployment automation, task
#          management, and monitoring integration
# Consumers: GitHub Actions deploy workflow, CloudWatch dashboards
# -----------------------------------------------------------------------------

output "ecs_service_name" {
  description = "Name of the ECS service running Error Triage containers. Used by CI/CD pipelines for service updates and task count monitoring."
  value       = module.ecs.service_name
}

output "ecs_service_arn" {
  description = "ARN of the ECS service. Used for IAM policy attachments, CloudWatch metric filtering, and AWS CLI service management operations."
  value       = module.ecs.service_arn
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster hosting the Error Triage service. Used for ECS CLI context configuration and cross-service cluster queries."
  value       = module.ecs.cluster_name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster. Used for CloudWatch Container Insights configuration and cluster-level monitoring dashboards."
  value       = module.ecs.cluster_arn
}

output "ecs_task_definition_arn" {
  description = "ARN of the active ECS task definition with revision number. Used to verify deployed task version and troubleshoot container configuration issues."
  value       = module.ecs.task_definition_arn
}

output "ecs_task_count" {
  description = "Current desired count of ECS tasks. Indicates baseline capacity configuration; actual running count may differ during auto-scaling events or deployments."
  value       = module.ecs.task_count
}

output "ecs_task_execution_role_arn" {
  description = "ARN of the IAM role used by ECS to launch tasks (ECR pull, secrets retrieval, log stream creation). Used for IAM policy audits and permission troubleshooting."
  value       = module.iam.task_execution_role_arn
}

output "ecs_task_role_arn" {
  description = "ARN of the IAM role used by application containers at runtime (Secrets Manager access, CloudWatch Logs). Used for application permission troubleshooting."
  value       = module.iam.task_role_arn
}

# -----------------------------------------------------------------------------
# Application Load Balancer Outputs
# Purpose: Provide ALB endpoints and identifiers for DNS configuration,
#          security group management, and external webhook routing
# Consumers: Route53 DNS records, webhook configuration (Vercel/GCP),
#            operational runbooks
# -----------------------------------------------------------------------------

output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer. Webhook ingress endpoint for Vercel and GCP POST /events requests. Configure as CNAME target for custom domain (e.g., error-triage-{env}.jiratest.com)."
  value       = module.ecs.alb_dns_name
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer. Used for CloudWatch metric queries, access log configuration, and AWS CLI ALB management operations."
  value       = module.ecs.alb_arn
}

output "alb_zone_id" {
  description = "Route53 hosted zone ID of the ALB for ALIAS record configuration. Use this value when creating Route53 A/AAAA records pointing to the ALB (zero-cost alternative to CNAME records)."
  value       = module.ecs.alb_zone_id
}

output "alb_target_group_arn" {
  description = "ARN of the ALB target group routing traffic to ECS tasks. Used for health check monitoring, target group attribute modifications, and debugging connection draining issues."
  value       = module.ecs.alb_target_group_arn
}

output "alb_security_group_id" {
  description = "Security group ID attached to the Application Load Balancer. Restricts inbound HTTPS to Vercel (76.76.21.0/24, 76.76.19.0/24) and GCP (35.191.0.0/16, 35.187.0.0/16, 108.177.96.0/19) webhook source IPs."
  value       = module.ecs.alb_security_group_id
}

output "alb_https_listener_arn" {
  description = "ARN of the HTTPS listener (port 443) with TLS 1.2+ termination. Used for listener rule modifications, certificate attachment verification, and traffic routing troubleshooting."
  value       = module.ecs.alb_https_listener_arn
}

# -----------------------------------------------------------------------------
# ElastiCache Redis Outputs
# Purpose: Export Redis connection details for application environment variable
#          injection and network access control configuration
# Consumers: ECS task definition environment variables, application config,
#            operational runbooks
# -----------------------------------------------------------------------------

output "redis_primary_endpoint" {
  description = "Primary endpoint address (host:port) of the ElastiCache Redis cluster. Use this endpoint for read/write operations (INCR, SETEX, GET). Format: 'jiratest-error-triage-redis-{env}.xxxxxx.cache.amazonaws.com:6379'."
  value       = module.redis.primary_endpoint_address
}

output "redis_configuration_endpoint" {
  description = "Configuration endpoint for Redis cluster management and monitoring. Use for cluster topology discovery in multi-node configurations."
  value       = module.redis.configuration_endpoint_address
}

output "redis_connection_string" {
  description = "Complete Redis connection string for application configuration. Includes host, port, and optional TLS parameters. Inject as REDIS_URL environment variable in ECS task definition."
  value       = module.redis.connection_string
  sensitive   = true
}

output "redis_port" {
  description = "Redis cluster port number (default: 6379). Used for security group rule configuration and firewall policy management."
  value       = module.redis.port
}

output "redis_security_group_id" {
  description = "Security group ID attached to the ElastiCache Redis cluster. Restricts inbound Redis connections (port 6379) to ECS task security group only (VPC-internal access)."
  value       = module.redis.security_group_id
}

# -----------------------------------------------------------------------------
# AWS Secrets Manager Outputs
# Purpose: List secret ARNs for IAM policy attachment enabling ECS task role
#          GetSecretValue permissions and secret rotation automation
# Consumers: IAM policy documents, secret rotation Lambda functions,
#            operational runbooks
# -----------------------------------------------------------------------------

output "jira_credentials_secret_arn" {
  description = "ARN of AWS Secrets Manager secret containing Jira Cloud API credentials (base_url, email, api_token). Grant ECS task execution role 'secretsmanager:GetSecretValue' permission to this ARN for application startup secret retrieval."
  value       = module.secrets.jira_credentials_secret_arn
}

output "webhook_secret_arn" {
  description = "ARN of AWS Secrets Manager secret storing webhook signature validation secrets (vercel_secret for HMAC-SHA256 verification, gcp_audience for OIDC JWT validation). Retrieved on-demand for webhook authentication."
  value       = module.secrets.webhook_secret_arn
}

output "mongodb_connection_string_secret_arn" {
  description = "ARN of AWS Secrets Manager secret containing MongoDB Atlas connection URI (mongodb+srv://...). Supports automatic rotation via Lambda function with 90-day schedule. Used for audit trail persistence and configuration versioning."
  value       = module.secrets.mongodb_connection_string_secret_arn
}

# -----------------------------------------------------------------------------
# CloudWatch Logs Outputs
# Purpose: Expose log group identifiers for centralized log aggregation
#          integration and CloudWatch Logs Insights query configuration
# Consumers: CloudWatch dashboard configurations, log aggregation tools
#            (Datadog, Splunk), operational query templates
# -----------------------------------------------------------------------------

output "log_group_name" {
  description = "CloudWatch Logs group name for Error Triage service logs. Format: '/aws/ecs/jiratest-error-triage-{env}'. Use for CloudWatch Logs Insights queries filtering by service and environment."
  value       = module.ecs.log_group_name
}

output "log_group_arn" {
  description = "ARN of the CloudWatch Logs group. Used for IAM policy attachments granting 'logs:PutLogEvents' permission, log subscription filter configuration, and cross-account log sharing."
  value       = module.ecs.log_group_arn
}

output "log_retention_days" {
  description = "Configured retention period for CloudWatch Logs in days (default: 90 days per Section 8.3.2.4). After retention period, logs automatically deleted to optimize storage costs."
  value       = module.ecs.log_retention_days
}

# -----------------------------------------------------------------------------
# ECR Repository Outputs
# Purpose: Provide Docker image repository URL for CI/CD pipeline image push
#          targets during build and deployment workflows
# Consumers: GitHub Actions build-push workflow, Docker CLI, operational
#            image management scripts
# -----------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "Full URL of the ECR repository for Error Triage Docker images. Format: '{account_id}.dkr.ecr.{region}.amazonaws.com/jiratest-error-triage'. Use as Docker image push target in CI/CD pipelines with tagging strategy: {git_sha}, {semantic_version}, {env}-{timestamp}."
  value       = module.ecs.ecr_repository_url
}

output "ecr_repository_arn" {
  description = "ARN of the ECR repository. Used for IAM policy attachments granting ECR push/pull permissions and lifecycle policy configuration."
  value       = module.ecs.ecr_repository_arn
}

# -----------------------------------------------------------------------------
# Networking Outputs
# Purpose: Export VPC configuration and NAT Gateway elastic IPs for MongoDB
#          Atlas IP whitelist configuration per Section 8.3.3
# Consumers: MongoDB Atlas network access configuration, operational network
#            troubleshooting, external service IP whitelisting
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID where all Error Triage infrastructure is deployed (jiratest-prod-vpc with 10.0.0.0/16 CIDR). Used for security group rule configuration and VPC peering setup."
  value       = data.aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "List of public subnet IDs across 3 availability zones (us-east-1a, 1b, 1c) hosting Application Load Balancer and NAT Gateways. Subnets have direct internet gateway routing for inbound webhook traffic."
  value       = data.aws_subnets.public.ids
}

output "private_subnet_ids" {
  description = "List of private subnet IDs across 3 availability zones hosting ECS Fargate tasks. Tasks have no direct internet access; outbound traffic routed via NAT Gateways for Jira API calls and MongoDB connections."
  value       = data.aws_subnets.private.ids
}

output "data_subnet_ids" {
  description = "List of private data-tier subnet IDs across 3 availability zones hosting ElastiCache Redis cluster. Isolated from public internet with security group restricting access to ECS tasks only."
  value       = data.aws_subnets.data.ids
}

output "nat_gateway_eips" {
  description = "List of Elastic IP addresses assigned to NAT Gateways for ECS task outbound internet access. CRITICAL: Add these IPs to MongoDB Atlas Network Access whitelist to enable database connections from application containers."
  value       = module.ecs.nat_gateway_elastic_ips
}

# -----------------------------------------------------------------------------
# Environment and Regional Configuration
# Purpose: Output environment identifier and AWS region for operational context
#          and multi-environment deployment management
# Consumers: Deployment scripts, environment-specific configuration loaders,
#            cost allocation reports
# -----------------------------------------------------------------------------

output "environment" {
  description = "Deployment environment identifier (dev, staging, production). Used for resource tagging, cost allocation, and environment-specific configuration selection in CI/CD workflows."
  value       = var.environment
}

output "aws_region" {
  description = "AWS region where infrastructure is deployed (default: us-east-1 per Section 8.2.1). Used for region-specific AWS CLI commands and cross-region replication configuration."
  value       = var.aws_region
}

# -----------------------------------------------------------------------------
# Service Endpoint Summary
# Purpose: Consolidated output for quick reference of all critical service
#          endpoints and connection strings
# Consumers: Operational runbooks, service health dashboards, documentation
# -----------------------------------------------------------------------------

output "service_endpoints" {
  description = "Map of all critical service endpoints for operational reference. Includes webhook ingress URL, Redis connection details, and CloudWatch log group for centralized troubleshooting."
  value = {
    webhook_url           = "https://${module.ecs.alb_dns_name}/events"
    health_check_url      = "https://${module.ecs.alb_dns_name}/healthz"
    metrics_url           = "https://${module.ecs.alb_dns_name}/metrics"
    redis_endpoint        = module.redis.primary_endpoint_address
    cloudwatch_log_group  = module.ecs.log_group_name
    ecr_repository        = module.ecs.ecr_repository_url
  }
}

# -----------------------------------------------------------------------------
# Deployment Information
# Purpose: Output deployment metadata for audit trail and version tracking
# Consumers: Deployment logs, infrastructure change management, compliance
#            audit reports
# -----------------------------------------------------------------------------

output "deployment_info" {
  description = "Deployment metadata including Terraform workspace, module versions, and deployment timestamp. Used for infrastructure audit trails and change management documentation."
  value = {
    terraform_workspace = terraform.workspace
    deployment_time     = timestamp()
    infrastructure_tags = {
      Environment = var.environment
      Service     = "error-triage"
      Project     = "jiratest"
      ManagedBy   = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Auto-Scaling Configuration
# Purpose: Output auto-scaling policy identifiers and thresholds for monitoring
#          and capacity management
# Consumers: CloudWatch dashboards, capacity planning reports, operational
#            scaling adjustment procedures
# -----------------------------------------------------------------------------

output "autoscaling_info" {
  description = "Auto-scaling configuration details including policy ARNs, target tracking thresholds, and capacity limits. Used for monitoring scaling events and adjusting capacity thresholds based on observed traffic patterns."
  value = {
    target_group_arn         = module.ecs.alb_target_group_arn
    min_capacity             = module.ecs.autoscaling_min_capacity
    max_capacity             = module.ecs.autoscaling_max_capacity
    target_cpu_utilization   = module.ecs.autoscaling_target_cpu_percentage
    scale_in_cooldown        = module.ecs.scale_in_cooldown_seconds
    scale_out_cooldown       = module.ecs.scale_out_cooldown_seconds
  }
}

# -----------------------------------------------------------------------------
# Security Configuration
# Purpose: Output security group IDs and IAM role ARNs for security audit and
#          network policy verification
# Consumers: Security audit reports, compliance verification procedures,
#            network troubleshooting
# -----------------------------------------------------------------------------

output "security_configuration" {
  description = "Security-related resource identifiers including security group IDs, IAM role ARNs, and secret ARNs. Used for security audits, IAM policy verification, and network access troubleshooting."
  value = {
    alb_security_group_id          = module.ecs.alb_security_group_id
    ecs_security_group_id          = module.ecs.ecs_security_group_id
    redis_security_group_id        = module.redis.security_group_id
    task_execution_role_arn        = module.iam.task_execution_role_arn
    task_role_arn                  = module.iam.task_role_arn
    jira_credentials_secret_arn    = module.secrets.jira_credentials_secret_arn
    webhook_secret_arn             = module.secrets.webhook_secret_arn
    mongodb_secret_arn             = module.secrets.mongodb_connection_string_secret_arn
  }
}

# -----------------------------------------------------------------------------
# Monitoring and Observability
# Purpose: Output monitoring resource identifiers for dashboard configuration
#          and alert rule setup
# Consumers: CloudWatch dashboard configurations, Grafana datasources, PagerDuty
#            integration, operational alerting workflows
# -----------------------------------------------------------------------------

output "monitoring_resources" {
  description = "Monitoring and observability resource identifiers including CloudWatch log groups, metric namespaces, and alarm ARNs. Used for centralized monitoring dashboard configuration and alerting rule setup."
  value = {
    cloudwatch_log_group     = module.ecs.log_group_name
    cloudwatch_log_stream    = "error-triage/*"
    metric_namespace         = "Jiratest/ErrorTriage"
    service_name             = module.ecs.service_name
    cluster_name             = module.ecs.cluster_name
  }
}
