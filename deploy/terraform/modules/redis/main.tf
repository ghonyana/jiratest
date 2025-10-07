# ==============================================================================
# ElastiCache Redis Module - Terraform Infrastructure as Code
# ==============================================================================
# Purpose: Provisions Amazon ElastiCache Redis cluster for Error Triage → Jira
#          Upserter service caching layer supporting frequency counters, event
#          deduplication, and comment rate limiting.
#
# Components:
#   - ElastiCache Subnet Group (Multi-AZ deployment across 3 availability zones)
#   - ElastiCache Parameter Group (Redis 7 configuration with persistence)
#   - Security Group (VPC-isolated access from ECS tasks only)
#   - ElastiCache Replication Group (Redis cluster with automatic failover)
#   - Random Password (optional AUTH token for authentication)
#
# Key Use Cases:
#   - Frequency counters: freq:{env}:{fingerprint} (5-minute TTL)
#   - Event deduplication: dedup:{event_id} (1-hour TTL)
#   - Comment rate limiting: comment_limit:{issue_key} (15-minute TTL)
# ==============================================================================

terraform {
  required_version = ">= 1.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

# ==============================================================================
# Random Password for Redis AUTH Token (Optional)
# ==============================================================================
# Generates a secure 32-character password for Redis AUTH authentication when
# auth_token_enabled variable is true. Provides additional security layer beyond
# security group isolation for production environments.
# ==============================================================================

resource "random_password" "redis_auth_token" {
  count = var.auth_token_enabled ? 1 : 0
  
  length  = 32
  special = true
  # Ensure password meets complexity requirements
  min_upper   = 4
  min_lower   = 4
  min_numeric = 4
  min_special = 4
  
  # Override special characters to Redis-compatible subset
  override_special = "!&#$^<>-"
}

# ==============================================================================
# ElastiCache Subnet Group
# ==============================================================================
# Defines subnet placement for Redis cluster nodes across private data subnets
# (10.0.21.0/24, 10.0.22.0/24, 10.0.23.0/24) spanning 3 availability zones for
# Multi-AZ high availability deployment.
# ==============================================================================

resource "aws_elasticache_subnet_group" "redis" {
  name        = "jiratest-redis-subnet-${var.environment}"
  description = "ElastiCache subnet group for Error Triage Redis cluster - ${var.environment}"
  
  # Reference private data subnets from VPC module
  subnet_ids = var.private_data_subnet_ids
  
  tags = merge(
    var.common_tags,
    {
      Name        = "jiratest-redis-subnet-${var.environment}"
      Environment = var.environment
      Purpose     = "ElastiCache Redis subnet group for caching layer"
      Tier        = "data"
    }
  )
}

# ==============================================================================
# ElastiCache Parameter Group
# ==============================================================================
# Configures Redis 7 engine parameters for persistence, eviction policies, and
# operational characteristics. Implements:
#   - AOF (Append-Only File) persistence with 1-second fsync for durability
#   - RDB snapshots with hourly backup schedule (3600 seconds, minimum 1 write)
#   - LRU eviction policy for automatic cache management at memory limit
#   - Connection timeout for idle client cleanup
# ==============================================================================

resource "aws_elasticache_parameter_group" "redis" {
  name        = "jiratest-redis-params-${var.environment}"
  description = "Redis 7 parameter group for Error Triage service - ${var.environment}"
  family      = "redis7"
  
  # AOF (Append-Only File) persistence configuration
  parameter {
    name  = "appendonly"
    value = var.appendonly
  }
  
  parameter {
    name  = "appendfsync"
    value = var.appendfsync
  }
  
  # RDB snapshot configuration - hourly backup with minimum 1 write
  parameter {
    name  = "save"
    value = "3600 1"
  }
  
  # Eviction policy - Least Recently Used for all keys when memory limit reached
  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }
  
  # Idle client connection timeout (5 minutes)
  parameter {
    name  = "timeout"
    value = "300"
  }
  
  tags = merge(
    var.common_tags,
    {
      Name        = "jiratest-redis-params-${var.environment}"
      Environment = var.environment
      Purpose     = "Redis 7 configuration for frequency counters and caching"
      RedisFamily = "redis7"
    }
  )
}

# ==============================================================================
# Security Group for ElastiCache Redis
# ==============================================================================
# Implements strict network isolation for Redis cluster:
#   - Ingress: TCP port 6379 only from ECS task security group
#   - Egress: Deny all (implicit) - Redis does not initiate outbound connections
#
# This security group prevents unauthorized access and data exfiltration by
# restricting Redis connectivity exclusively to application containers.
# ==============================================================================

resource "aws_security_group" "redis" {
  name        = "jiratest-${var.environment}-redis-sg"
  description = "Security group for ElastiCache Redis cluster - restricts access to ECS tasks only"
  vpc_id      = var.vpc_id
  
  # Ingress rule: Allow Redis connections only from ECS task security group
  ingress {
    description     = "Redis access from ECS Fargate tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.ecs_security_group_id]
  }
  
  # Egress: No explicit egress rules (implicit deny-all)
  # Redis cluster does not initiate outbound connections, preventing data exfiltration
  
  tags = merge(
    var.common_tags,
    {
      Name        = "jiratest-${var.environment}-redis-sg"
      Environment = var.environment
      Purpose     = "ElastiCache Redis network isolation - ECS tasks only"
      Tier        = "data"
    }
  )
}

# ==============================================================================
# ElastiCache Replication Group (Redis Cluster)
# ==============================================================================
# Provisions Redis cluster with the following characteristics:
#
# Multi-AZ Configuration (Production):
#   - Primary node + read replica across 2 availability zones
#   - Automatic failover enabled (60-90 second promotion time)
#   - Survives complete AZ failure without data loss
#
# Single-Node Configuration (Dev/Staging):
#   - Cost-optimized single-node deployment
#   - Automatic failover disabled
#
# Persistence:
#   - AOF with 1-second fsync (parameter group)
#   - Daily RDB snapshots with 1-day retention
#   - Snapshot window: 03:00-05:00 UTC (low-traffic period)
#
# Security:
#   - Transit encryption (TLS) enabled
#   - Optional AUTH token authentication
#   - Security group restricts access to ECS tasks
#
# Maintenance:
#   - Maintenance window: Sunday 05:00-07:00 UTC
#   - Auto minor version upgrades enabled for security patches
#   - apply_immediately=false for controlled deployment
# ==============================================================================

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "jiratest-error-triage-redis-${var.environment}"
  replication_group_description = "Redis cluster for frequency counters, event deduplication, comment rate limiting - ${var.environment}"
  
  # Redis engine configuration
  engine         = "redis"
  engine_version = var.engine_version
  port           = 6379
  
  # Node configuration - environment-specific sizing
  node_type             = var.node_type
  num_cache_clusters    = var.num_cache_nodes
  parameter_group_name  = aws_elasticache_parameter_group.redis.name
  
  # Network configuration
  subnet_group_name = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]
  
  # High availability configuration
  automatic_failover_enabled = var.automatic_failover_enabled
  multi_az_enabled          = var.automatic_failover_enabled
  
  # Security configuration
  transit_encryption_enabled = var.transit_encryption_enabled
  auth_token                = var.auth_token_enabled ? random_password.redis_auth_token[0].result : null
  at_rest_encryption_enabled = var.at_rest_encryption_enabled
  
  # Backup configuration
  snapshot_retention_limit = var.snapshot_retention_limit
  snapshot_window         = "03:00-05:00"  # UTC - low-traffic window
  
  # Maintenance configuration
  maintenance_window       = "sun:05:00-sun:07:00"  # UTC - post-snapshot window
  auto_minor_version_upgrade = true
  apply_immediately         = false  # Controlled deployment during maintenance window
  
  # Notifications
  notification_topic_arn = var.sns_topic_arn
  
  # Logging configuration
  log_delivery_configuration {
    destination      = var.cloudwatch_log_group_name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }
  
  log_delivery_configuration {
    destination      = var.cloudwatch_log_group_name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "engine-log"
  }
  
  # Resource tags
  tags = merge(
    var.common_tags,
    {
      Name        = "jiratest-error-triage-redis-${var.environment}"
      Environment = var.environment
      Purpose     = "Redis cluster for frequency tracking, deduplication, rate limiting"
      Tier        = "data"
      Service     = "error-triage"
    }
  )
  
  # Lifecycle management
  lifecycle {
    # Prevent accidental deletion of production cluster
    prevent_destroy = var.environment == "production" ? true : false
    
    # Ignore engine version changes to allow controlled upgrades
    ignore_changes = [engine_version]
  }
}

# ==============================================================================
# Outputs
# ==============================================================================

output "redis_primary_endpoint" {
  description = "Primary endpoint for Redis cluster connection (host:port format)"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "redis_port" {
  description = "Redis cluster port number (default: 6379)"
  value       = aws_elasticache_replication_group.redis.port
}

output "redis_reader_endpoint" {
  description = "Reader endpoint for Redis cluster (read replicas)"
  value       = aws_elasticache_replication_group.redis.reader_endpoint_address
}

output "redis_configuration_endpoint" {
  description = "Configuration endpoint for Redis cluster (cluster mode enabled)"
  value       = aws_elasticache_replication_group.redis.configuration_endpoint_address
}

output "redis_security_group_id" {
  description = "Security group ID for Redis cluster"
  value       = aws_security_group.redis.id
}

output "redis_auth_token" {
  description = "AUTH token for Redis authentication (sensitive)"
  value       = var.auth_token_enabled ? random_password.redis_auth_token[0].result : null
  sensitive   = true
}

output "redis_connection_string" {
  description = "Full Redis connection string for application configuration (sensitive)"
  value = var.auth_token_enabled ? format(
    "rediss://%s:%s@%s:%d",
    "default",
    random_password.redis_auth_token[0].result,
    aws_elasticache_replication_group.redis.primary_endpoint_address,
    aws_elasticache_replication_group.redis.port
  ) : format(
    "redis://%s:%d",
    aws_elasticache_replication_group.redis.primary_endpoint_address,
    aws_elasticache_replication_group.redis.port
  )
  sensitive = true
}

output "redis_arn" {
  description = "ARN of the Redis replication group"
  value       = aws_elasticache_replication_group.redis.arn
}
