# ==============================================================================
# Redis ElastiCache Module - Input Variables
# ==============================================================================
# Purpose: Define comprehensive input parameters for ElastiCache Redis cluster
#          provisioning supporting the Error Triage → Jira Upserter service
# Module: deploy/terraform/modules/redis
# ==============================================================================

# ------------------------------------------------------------------------------
# Environment Configuration
# ------------------------------------------------------------------------------

variable "environment" {
  description = "Deployment environment identifier (dev, staging, production). Determines node sizing, Multi-AZ configuration, and resource naming conventions."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: dev, staging, production."
  }
}

# ------------------------------------------------------------------------------
# VPC Networking Configuration
# ------------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC identifier where Redis cluster will be deployed. Must match existing jiratest-prod-vpc infrastructure (10.0.0.0/16)."
  type        = string

  validation {
    condition     = can(regex("^vpc-[a-f0-9]+$", var.vpc_id))
    error_message = "VPC ID must be a valid AWS VPC identifier format (vpc-xxxxxxxxx)."
  }
}

variable "private_data_subnet_ids" {
  description = <<-EOT
    List of private data subnet IDs for Multi-AZ Redis deployment across 3 availability zones.
    Expected subnets: 10.0.21.0/24 (us-east-1a), 10.0.22.0/24 (us-east-1b), 10.0.23.0/24 (us-east-1c).
    Redis cluster will be distributed across these subnets for high availability per Section 8.3.3.1.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.private_data_subnet_ids) >= 2
    error_message = "At least 2 private data subnet IDs required for Multi-AZ deployment."
  }

  validation {
    condition     = alltrue([for id in var.private_data_subnet_ids : can(regex("^subnet-[a-f0-9]+$", id))])
    error_message = "All subnet IDs must be valid AWS subnet identifier format (subnet-xxxxxxxxx)."
  }
}

variable "ecs_security_group_id" {
  description = <<-EOT
    Security group ID for ECS Fargate tasks (jiratest-{env}-ecs-sg).
    Redis cluster security group will allow inbound connections exclusively from this security group
    to restrict access to application containers only per Section 8.3.3.2.
  EOT
  type        = string

  validation {
    condition     = can(regex("^sg-[a-f0-9]+$", var.ecs_security_group_id))
    error_message = "ECS security group ID must be a valid AWS security group identifier format (sg-xxxxxxxxx)."
  }
}

# ------------------------------------------------------------------------------
# Redis Cluster Configuration
# ------------------------------------------------------------------------------

variable "node_type" {
  description = <<-EOT
    ElastiCache node type determining CPU, memory, and network capacity.
    Environment-specific defaults align with Section 8.3.2.2 sizing:
      - Development:  cache.t4g.micro  (0.5 GB memory, burst CPU, cost-optimized)
      - Staging:      cache.t4g.small  (0.5 GB memory, single-node, pre-production testing)
      - Production:   cache.t4g.medium (1.2 GB memory, network-optimized, ~20,000 ops/sec throughput)
    
    AWS Graviton2-based t4g family provides 20% cost reduction vs x86 equivalents.
  EOT
  type        = string
  default     = null

  validation {
    condition     = var.node_type == null || can(regex("^cache\\.(t4g|t3|m6g|r6g)\\.(micro|small|medium|large|xlarge)$", var.node_type))
    error_message = "Node type must be a valid ElastiCache instance type (e.g., cache.t4g.micro, cache.t4g.small, cache.t4g.medium)."
  }
}

variable "engine_version" {
  description = <<-EOT
    Redis engine version. Default 7.2 provides enhanced ACL support, performance improvements
    in atomic operations (INCR, SETEX), and TLS in-transit encryption per Section 8.3.2.2.
    Supports Redis 7.0, 7.2, 7.4, and 8.0 family versions.
  EOT
  type        = string
  default     = "7.2"

  validation {
    condition     = can(regex("^(7\\.0|7\\.2|7\\.4|8\\.0)$", var.engine_version))
    error_message = "Engine version must be a supported Redis version (7.0, 7.2, 7.4, or 8.0)."
  }
}

variable "num_cache_nodes" {
  description = <<-EOT
    Number of cache nodes in replication group.
    Configuration per Section 8.3.2.2:
      - Development/Staging: 1 (single-node, no replication)
      - Production:          2 (primary + read replica across 2 AZs for automatic failover)
    
    Multi-AZ production deployment enables 60-90 second automatic failover per Section 8.3.4.1.
  EOT
  type        = number
  default     = null

  validation {
    condition     = var.num_cache_nodes == null || (var.num_cache_nodes >= 1 && var.num_cache_nodes <= 6)
    error_message = "Number of cache nodes must be between 1 and 6."
  }
}

variable "automatic_failover_enabled" {
  description = <<-EOT
    Enable automatic failover for Multi-AZ deployment with replica promotion.
    Configuration per Section 8.3.4.1:
      - Production:          true  (automatic failover within 60-90 seconds)
      - Development/Staging: false (single-node deployment, no replica)
    
    Requires num_cache_nodes >= 2. When enabled, ElastiCache automatically promotes replica
    to primary during node failure and updates DNS endpoint.
  EOT
  type        = bool
  default     = null
}

# ------------------------------------------------------------------------------
# Persistence Configuration
# ------------------------------------------------------------------------------

variable "appendonly" {
  description = <<-EOT
    Enable Redis AOF (Append-Only File) persistence for durability.
    Default 'yes' enables AOF to persist write operations to disk, allowing data recovery
    after restart per Section 8.3.2.2. Combined with RDB snapshots for comprehensive persistence strategy.
  EOT
  type        = string
  default     = "yes"

  validation {
    condition     = contains(["yes", "no"], var.appendonly)
    error_message = "Appendonly must be 'yes' or 'no'."
  }
}

variable "appendfsync" {
  description = <<-EOT
    AOF fsync policy controlling durability vs performance trade-off.
    Default 'everysec' syncs to disk every second per Section 8.3.2.2, balancing:
      - Data safety:   Maximum 1 second of writes lost on failure
      - Performance:   Minimal impact on write latency vs 'always' option
    
    Options: 'always' (sync every write, slowest), 'everysec' (recommended), 'no' (OS-controlled, fastest but riskiest).
  EOT
  type        = string
  default     = "everysec"

  validation {
    condition     = contains(["always", "everysec", "no"], var.appendfsync)
    error_message = "Appendfsync must be one of: always, everysec, no."
  }
}

variable "snapshot_retention_limit" {
  description = <<-EOT
    Number of daily RDB snapshot backups to retain (0-35 days).
    Default 1 retains previous day's snapshot for recovery scenarios per Section 8.3.2.2.
    Set to 0 to disable RDB snapshots (not recommended for production).
    
    Snapshots occur during snapshot_window (03:00-05:00 UTC low-traffic period).
  EOT
  type        = number
  default     = 1

  validation {
    condition     = var.snapshot_retention_limit >= 0 && var.snapshot_retention_limit <= 35
    error_message = "Snapshot retention limit must be between 0 and 35 days."
  }
}

# ------------------------------------------------------------------------------
# Security Configuration
# ------------------------------------------------------------------------------

variable "transit_encryption_enabled" {
  description = <<-EOT
    Enable TLS in-transit encryption for Redis connections.
    Default true enforces TLS 1.2+ encryption per Section 8.3.6.1, protecting:
      - Frequency counter operations (INCR, EXPIRE)
      - Event deduplication data (SETNX)
      - Comment rate limit timestamps (SETEX, GET)
    
    Required for SOC 2 compliance. Application must use TLS-enabled Redis client.
  EOT
  type        = bool
  default     = true
}

variable "auth_token_enabled" {
  description = <<-EOT
    Enable Redis AUTH token authentication for connection security.
    Default true generates random 32-character token for Redis AUTH command per Section 8.3.6.2.
    Provides additional authentication layer beyond VPC security group isolation.
    
    When enabled, application connection string must include auth token parameter.
  EOT
  type        = bool
  default     = true
}

# ------------------------------------------------------------------------------
# Tagging Configuration
# ------------------------------------------------------------------------------

variable "common_tags" {
  description = <<-EOT
    Common resource tags applied to all Redis module resources for organization, cost allocation, and compliance.
    Recommended tags per Section 8.3.5:
      - Environment: dev, staging, production
      - Service:     error-triage
      - Project:     jiratest-error-triage
      - Team:        platform-engineering
      - CostCenter:  infrastructure
  EOT
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------------------
# Local Variables for Environment-Specific Defaults
# ------------------------------------------------------------------------------

locals {
  # Environment-specific node type defaults per Section 8.3.2.2
  default_node_type = {
    dev        = "cache.t4g.micro"
    staging    = "cache.t4g.small"
    production = "cache.t4g.medium"
  }

  # Environment-specific node count defaults
  default_num_cache_nodes = {
    dev        = 1
    staging    = 1
    production = 2
  }

  # Environment-specific automatic failover defaults
  default_automatic_failover = {
    dev        = false
    staging    = false
    production = true
  }

  # Resolved configuration values with environment-specific fallbacks
  resolved_node_type              = var.node_type != null ? var.node_type : local.default_node_type[var.environment]
  resolved_num_cache_nodes        = var.num_cache_nodes != null ? var.num_cache_nodes : local.default_num_cache_nodes[var.environment]
  resolved_automatic_failover     = var.automatic_failover_enabled != null ? var.automatic_failover_enabled : local.default_automatic_failover[var.environment]
}
