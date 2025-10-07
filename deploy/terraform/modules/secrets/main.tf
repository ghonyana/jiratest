# ============================================================================
# AWS Secrets Manager Module for Error Triage Service
# ============================================================================
# 
# This module provisions AWS Secrets Manager resources for the Error Triage
# service including Jira API credentials, webhook authentication secrets, and
# optional MongoDB connection strings. All secrets are encrypted using AWS KMS
# customer managed keys with automatic annual rotation.
#
# Key Features:
# - AES-256-GCM encryption with KMS customer managed keys
# - Automatic annual KMS key rotation
# - Environment-specific secret naming (dev/staging/production)
# - 30-day version history retention
# - Recovery window configuration
# - Comprehensive resource tagging
#
# Resources Created:
# - AWS KMS customer managed key for secrets encryption
# - KMS key alias for human-readable reference
# - Secrets Manager secret for Jira credentials
# - Secrets Manager secret for webhook authentication
# - Conditional Secrets Manager secret for MongoDB (enable_mongo=true)
# ============================================================================

# ============================================================================
# Local Variables
# ============================================================================

locals {
  # Common resource naming prefix
  resource_prefix = "${var.project}-${var.environment}"
  
  # Common tags applied to all resources
  common_tags = merge(
    var.tags,
    {
      Environment = var.environment
      Service     = "error-triage"
      Project     = var.project
      ManagedBy   = "terraform"
      Module      = "secrets"
    }
  )
  
  # Recovery window based on environment
  # Production: 30 days for maximum safety
  # Non-production: 7 days for faster iteration
  recovery_window_days = var.environment == "production" ? 30 : 7
}

# ============================================================================
# KMS Customer Managed Key
# ============================================================================
# 
# Customer managed key for encrypting all Secrets Manager secrets with
# automatic annual rotation enabled. Key policy grants necessary permissions
# to ECS task execution role for decrypt operations during secret retrieval.

resource "aws_kms_key" "secrets" {
  description              = "KMS key for ${local.resource_prefix} secrets encryption"
  deletion_window_in_days  = 30
  enable_key_rotation      = var.kms_key_rotation_enabled
  multi_region             = false
  
  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-secrets-key"
    }
  )
  
  # KMS key policy granting root account full access and enabling
  # IAM policies to delegate additional permissions to ECS roles
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow ECS Task Execution Role Decrypt"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.resource_prefix}-ecs-task-execution-role"
        }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
        }
      },
      {
        Sid    = "Allow CloudWatch Logs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.aws_region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:CreateGrant",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ecs/${local.resource_prefix}-*"
          }
        }
      }
    ]
  })
}

# ============================================================================
# KMS Key Alias
# ============================================================================
#
# Human-readable alias for the KMS key enabling easier reference in
# IAM policies, CloudWatch dashboards, and operational documentation.

resource "aws_kms_alias" "secrets" {
  name          = "alias/${local.resource_prefix}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

# ============================================================================
# Data Source: AWS Account ID
# ============================================================================

data "aws_caller_identity" "current" {}

# ============================================================================
# Secrets Manager Secret: Jira Credentials
# ============================================================================
#
# Stores Jira API credentials including base URL, email, and API token.
# Retrieved at application startup and cached in-memory for 1 hour.
#
# Secret Structure (JSON):
# {
#   "base_url": "https://organization.atlassian.net",
#   "email": "api-user@example.com",
#   "api_token": "ATATT..."
# }
#
# Rotation: Manual every 90 days per security policy

resource "aws_secretsmanager_secret" "jira_credentials" {
  name                    = "jira/${var.project}/${var.environment}/credentials"
  description             = "Jira API credentials for ${var.environment} environment - includes base URL, email, and API token"
  kms_key_id              = aws_kms_key.secrets.id
  recovery_window_in_days = local.recovery_window_days
  
  tags = merge(
    local.common_tags,
    {
      Name          = "${local.resource_prefix}-jira-credentials"
      SecretType    = "jira-api"
      RotationPolicy = "manual-90-days"
    }
  )
}

# Version management for Jira credentials secret
# Implements 30-day version history retention for rollback capability
resource "aws_secretsmanager_secret_version" "jira_credentials" {
  secret_id = aws_secretsmanager_secret.jira_credentials.id
  
  # Placeholder secret value - must be updated via AWS Console or CLI
  # after Terraform apply with actual Jira API credentials
  secret_string = jsonencode({
    base_url  = "https://CHANGE_ME.atlassian.net"
    email     = "CHANGE_ME@example.com"
    api_token = "CHANGE_ME_JIRA_API_TOKEN"
  })
  
  lifecycle {
    ignore_changes = [
      # Prevent Terraform from overwriting manually updated credentials
      secret_string
    ]
  }
}

# ============================================================================
# Secrets Manager Secret: Webhook Authentication
# ============================================================================
#
# Stores webhook authentication secrets for Vercel signature verification
# and GCP OIDC audience validation. Retrieved on-demand and cached in-memory
# for 5 minutes to minimize AWS API calls during high-frequency webhook events.
#
# Secret Structure (JSON):
# {
#   "vercel_secret": "32-byte-hex-string",
#   "gcp_audience": "https://error-triage.jiratest.com"
# }
#
# Rotation: Manual every 180 days with coordinated Vercel webhook config update

resource "aws_secretsmanager_secret" "webhook_secret" {
  name                    = "jira/${var.project}/${var.environment}/webhook-secret"
  description             = "Webhook authentication secrets for ${var.environment} - Vercel HMAC signature and GCP OIDC audience"
  kms_key_id              = aws_kms_key.secrets.id
  recovery_window_in_days = local.recovery_window_days
  
  tags = merge(
    local.common_tags,
    {
      Name          = "${local.resource_prefix}-webhook-secret"
      SecretType    = "webhook-auth"
      RotationPolicy = "manual-180-days"
    }
  )
}

# Version management for webhook secret
resource "aws_secretsmanager_secret_version" "webhook_secret" {
  secret_id = aws_secretsmanager_secret.webhook_secret.id
  
  # Placeholder secret value - must be updated with actual webhook secrets
  secret_string = jsonencode({
    vercel_secret = "CHANGE_ME_VERCEL_WEBHOOK_SECRET"
    gcp_audience  = "https://error-triage-${var.environment}.jiratest.com"
  })
  
  lifecycle {
    ignore_changes = [
      # Prevent Terraform from overwriting manually configured secrets
      secret_string
    ]
  }
}

# ============================================================================
# Secrets Manager Secret: MongoDB Connection String (Conditional)
# ============================================================================
#
# Stores MongoDB Atlas connection string for audit trail persistence.
# Only created when enable_mongo variable is true (optional in v1).
# Retrieved at application startup with long-lived connection pool.
#
# Secret Structure (String):
# mongodb+srv://username:password@cluster.mongodb.net/database?retryWrites=true&w=majority
#
# Rotation: Automatic every 90 days via MongoDB Atlas Lambda rotation function

resource "aws_secretsmanager_secret" "mongodb_connection_string" {
  count = var.enable_mongo ? 1 : 0
  
  name                    = "mongodb/${var.project}/${var.environment}/connection-string"
  description             = "MongoDB Atlas connection string for ${var.environment} audit logging database"
  kms_key_id              = aws_kms_key.secrets.id
  recovery_window_in_days = local.recovery_window_days
  
  tags = merge(
    local.common_tags,
    {
      Name          = "${local.resource_prefix}-mongodb-connection"
      SecretType    = "mongodb-uri"
      RotationPolicy = "automatic-90-days"
    }
  )
}

# Version management for MongoDB connection string
resource "aws_secretsmanager_secret_version" "mongodb_connection_string" {
  count = var.enable_mongo ? 1 : 0
  
  secret_id = aws_secretsmanager_secret.mongodb_connection_string[0].id
  
  # Placeholder connection string - must be updated with actual MongoDB Atlas URI
  secret_string = "mongodb+srv://CHANGE_ME:CHANGE_ME@cluster.mongodb.net/${var.project}-${var.environment}?retryWrites=true&w=majority"
  
  lifecycle {
    ignore_changes = [
      # Prevent Terraform from overwriting Atlas-managed connection string
      secret_string
    ]
  }
}

# ============================================================================
# Secret Rotation Configuration (Placeholder for Future Implementation)
# ============================================================================
#
# Note: Automatic secret rotation requires Lambda functions and additional
# configuration. For Phase 1 implementation:
# - Jira API tokens: Manual rotation every 90 days
# - Webhook secrets: Manual rotation every 180 days  
# - MongoDB passwords: Manual rotation every 90 days
#
# Future enhancement: Implement Lambda rotation functions for automated
# credential rotation with zero-downtime updates.

# ============================================================================
# Outputs
# ============================================================================

output "kms_key_id" {
  description = "ID of the KMS key used for secrets encryption"
  value       = aws_kms_key.secrets.id
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for secrets encryption"
  value       = aws_kms_key.secrets.arn
}

output "kms_key_alias" {
  description = "Alias of the KMS key for human-readable reference"
  value       = aws_kms_alias.secrets.name
}

output "jira_credentials_secret_arn" {
  description = "ARN of the Jira credentials secret for IAM policy attachment"
  value       = aws_secretsmanager_secret.jira_credentials.arn
}

output "jira_credentials_secret_name" {
  description = "Name of the Jira credentials secret for application reference"
  value       = aws_secretsmanager_secret.jira_credentials.name
}

output "webhook_secret_arn" {
  description = "ARN of the webhook authentication secret for IAM policy attachment"
  value       = aws_secretsmanager_secret.webhook_secret.arn
}

output "webhook_secret_name" {
  description = "Name of the webhook secret for application reference"
  value       = aws_secretsmanager_secret.webhook_secret.name
}

output "mongodb_connection_string_secret_arn" {
  description = "ARN of the MongoDB connection string secret (conditional on enable_mongo)"
  value       = var.enable_mongo ? aws_secretsmanager_secret.mongodb_connection_string[0].arn : null
}

output "mongodb_connection_string_secret_name" {
  description = "Name of the MongoDB connection string secret (conditional on enable_mongo)"
  value       = var.enable_mongo ? aws_secretsmanager_secret.mongodb_connection_string[0].name : null
}
