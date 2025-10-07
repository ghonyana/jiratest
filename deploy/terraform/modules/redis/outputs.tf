# Redis ElastiCache Module Outputs
# Exposes connection endpoints, resource identifiers, and security group references
# for integration with root Terraform module and ECS task configuration

# =============================================================================
# Primary Endpoint Outputs - Connection Information
# =============================================================================

output "redis_primary_endpoint_address" {
  description = "Primary endpoint hostname for Redis connections from ECS tasks; format: {cluster-name}.{random}.cache.amazonaws.com for REDIS_HOST environment variable"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "redis_port" {
  description = "Redis port number (6379) for connection strings"
  value       = aws_elasticache_replication_group.redis.port
}

output "redis_configuration_endpoint" {
  description = "Configuration endpoint for cluster mode Redis (unused for replication group mode)"
  value       = aws_elasticache_replication_group.redis.configuration_endpoint_address
}

# =============================================================================
# Replication Group Outputs - Resource Identification
# =============================================================================

output "replication_group_id" {
  description = "Replication group identifier for CloudWatch metrics filtering and resource tagging"
  value       = aws_elasticache_replication_group.redis.id
}

output "replication_group_arn" {
  description = "ARN of ElastiCache replication group for IAM policy attachment"
  value       = aws_elasticache_replication_group.redis.arn
}

output "replication_group_member_clusters" {
  description = "List of cache cluster IDs in replication group (primary + replicas) for individual node monitoring"
  value       = aws_elasticache_replication_group.redis.member_clusters
}

# =============================================================================
# Security Group Output - Network Policy Configuration
# =============================================================================

output "redis_security_group_id" {
  description = "Security group ID restricting Redis access to ECS tasks for network policy configuration"
  value       = aws_security_group.redis.id
}

# =============================================================================
# Parameter Group Output - Configuration Management
# =============================================================================

output "redis_parameter_group_name" {
  description = "Parameter group name for Redis configuration tuning reference"
  value       = aws_elasticache_parameter_group.redis.name
}
