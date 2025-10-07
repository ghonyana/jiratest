# ============================================================================
# Terraform Outputs for ECS Module
# ============================================================================
# Purpose: Export ECS module resource identifiers, endpoints, and configuration
#          values for integration with root Terraform module and external systems
# Usage: Values consumed by CI/CD pipelines, monitoring dashboards, and 
#        documentation generation
# ============================================================================

# ============================================================================
# ECS Service Outputs
# ============================================================================
# Service outputs enable deployment automation via CI/CD pipelines and provide
# resource identifiers for service management operations

output "ecs_service_name" {
  description = "Name of the ECS service for deployment automation and task updates in CI/CD pipelines"
  value       = aws_ecs_service.main.name
}

output "ecs_service_arn" {
  description = "ARN of the ECS service for IAM policy attachment and cross-stack resource references"
  value       = aws_ecs_service.main.arn
}

output "ecs_service_id" {
  description = "ID of the ECS service for Terraform resource references and dependency management"
  value       = aws_ecs_service.main.id
}

# ============================================================================
# ECS Cluster Outputs
# ============================================================================
# Cluster outputs support multi-service cluster management and CloudWatch
# Container Insights integration for observability

output "ecs_cluster_name" {
  description = "Name of the ECS cluster for service discovery and CloudWatch Container Insights namespace"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster for cross-stack references and IAM policy attachment"
  value       = aws_ecs_cluster.main.arn
}

output "ecs_cluster_id" {
  description = "ID of the ECS cluster for Terraform resource references and cluster management operations"
  value       = aws_ecs_cluster.main.id
}

# ============================================================================
# ECS Task Definition Outputs
# ============================================================================
# Task definition outputs enable version tracking, rollback operations, and
# deployment history auditing

output "ecs_task_definition_arn" {
  description = "ARN of the current task definition including revision number for deployment tracking and rollback operations"
  value       = aws_ecs_task_definition.app.arn
}

output "ecs_task_definition_family" {
  description = "Task definition family name for version history queries and task definition management"
  value       = aws_ecs_task_definition.app.family
}

output "ecs_task_definition_revision" {
  description = "Current task definition revision number for version tracking and audit trail documentation"
  value       = aws_ecs_task_definition.app.revision
}

# ============================================================================
# Application Load Balancer Outputs
# ============================================================================
# ALB outputs provide webhook ingress endpoint configuration for external
# system integration (Vercel webhook URL, GCP Pub/Sub push endpoint) and
# Route53 DNS alias record creation

output "alb_arn" {
  description = "ARN of the Application Load Balancer for CloudWatch metrics filtering and resource tagging"
  value       = aws_lb.main.arn
}

output "alb_dns_name" {
  description = "DNS name of the ALB for webhook endpoint configuration (Vercel Log Drain, GCP Pub/Sub push subscription) and Route53 alias record creation"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Route53 zone ID of the ALB for creating alias records pointing to error-triage.jiratest.com"
  value       = aws_lb.main.zone_id
}

# ============================================================================
# Target Group Outputs
# ============================================================================
# Target group outputs support health check monitoring, ALB listener rule
# configuration, and CloudWatch metrics filtering

output "alb_target_group_arn" {
  description = "ARN of the ALB target group for listener rule attachment and health check configuration"
  value       = aws_lb_target_group.app.arn
}

output "alb_target_group_name" {
  description = "Name of the target group for CloudWatch metrics filtering (RequestCountPerTarget, TargetResponseTime) and operational dashboards"
  value       = aws_lb_target_group.app.name
}

# ============================================================================
# Security Group Outputs
# ============================================================================
# Security group outputs enable firewall rule management and network policy
# enforcement across the infrastructure stack

output "alb_security_group_id" {
  description = "Security group ID for the ALB controlling inbound HTTPS traffic from Vercel (76.76.21.0/24) and GCP Pub/Sub (35.191.0.0/16) webhook sources; passed through from root module for network policy consistency"
  value       = var.alb_security_group_id
}

output "ecs_security_group_id" {
  description = "Security group ID for ECS tasks controlling inbound HTTP traffic from ALB and outbound access to Redis (port 6379), Jira API (HTTPS 443), and MongoDB Atlas (port 27017); passed through from root module for network policy consistency"
  value       = var.ecs_security_group_id
}
