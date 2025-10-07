# ==============================================================================
# Terraform Outputs - AWS Secrets Manager Secret ARNs and KMS Key References
# ==============================================================================
# Purpose: Export secret ARNs for IAM policy attachment and ECS task definition
#          secret injection. Provides KMS key references for encryption config.
#
# Module: deploy/terraform/modules/secrets
# Service: Error Triage → Jira Upserter
# ==============================================================================

# ------------------------------------------------------------------------------
# Jira Credentials Secret Outputs
# ------------------------------------------------------------------------------
# Jira API credentials (base_url, email, api_token) stored in AWS Secrets Manager
# Used by ECS task definition to inject credentials as environment variables and
# by IAM module to grant secretsmanager:GetSecretValue permissions to task role

output "jira_credentials_secret_arn" {
  description = "ARN of the Jira credentials secret for IAM policy attachment and ECS task definition secret injection. Contains base_url, email, and api_token fields for Atlassian Jira Cloud API authentication."
  value       = aws_secretsmanager_secret.jira_credentials.arn
  sensitive   = true
}

output "jira_credentials_secret_name" {
  description = "Name of the Jira credentials secret for CloudWatch logging, documentation, and application configuration reference. Format: jira/jiratest/{environment}/credentials"
  value       = aws_secretsmanager_secret.jira_credentials.name
}

# ------------------------------------------------------------------------------
# Webhook Authentication Secret Outputs
# ------------------------------------------------------------------------------
# Webhook secrets for Vercel signature validation and GCP OIDC audience validation
# Used by application to authenticate incoming webhook requests from Vercel Log Drain
# and GCP Pub/Sub push subscriptions

output "webhook_secret_arn" {
  description = "ARN of the webhook authentication secret for runtime secret retrieval and IAM policy attachment. Contains vercel_secret for HMAC signature validation and gcp_audience for OIDC token verification."
  value       = aws_secretsmanager_secret.webhook_secret.arn
  sensitive   = true
}

output "webhook_secret_name" {
  description = "Name of the webhook authentication secret for application configuration reference and CloudWatch logging. Format: jira/jiratest/{environment}/webhook-secret"
  value       = aws_secretsmanager_secret.webhook_secret.name
}

# ------------------------------------------------------------------------------
# MongoDB Connection String Secret Outputs (Conditional)
# ------------------------------------------------------------------------------
# MongoDB Atlas connection string for optional audit log storage
# Only created when enable_mongo variable is true
# Returns null when MongoDB is disabled to avoid referencing non-existent resources

output "mongodb_connection_string_secret_arn" {
  description = "ARN of the MongoDB Atlas connection string secret (created only when enable_mongo=true). Used for pymongo client initialization with connection string containing authentication credentials and cluster endpoint. Returns null when MongoDB is disabled."
  value       = var.enable_mongo ? aws_secretsmanager_secret.mongodb_connection_string[0].arn : null
  sensitive   = true
}

output "mongodb_connection_string_secret_name" {
  description = "Name of the MongoDB connection string secret for application configuration (created only when enable_mongo=true). Format: mongodb/jiratest/{environment}/connection-string. Returns null when MongoDB is disabled."
  value       = var.enable_mongo ? aws_secretsmanager_secret.mongodb_connection_string[0].name : null
}

# ------------------------------------------------------------------------------
# KMS Key Outputs
# ------------------------------------------------------------------------------
# Customer managed KMS key for secrets encryption with automatic annual rotation
# Used by IAM module to grant kms:Decrypt permissions and by other modules
# for cross-module encryption configuration

output "kms_key_arn" {
  description = "ARN of the KMS customer managed key for IAM decrypt permissions and cross-module encryption configuration. Enables ECS task role to decrypt Secrets Manager secrets encrypted with this key. Key has automatic annual rotation enabled."
  value       = aws_kms_key.secrets.arn
}

output "kms_key_id" {
  description = "ID of the KMS customer managed key for resource references in other Terraform modules. Used for KMS key policy attachments and encryption configuration in dependent resources."
  value       = aws_kms_key.secrets.id
}

# ------------------------------------------------------------------------------
# Output Usage Notes
# ------------------------------------------------------------------------------
# IAM Module Integration:
#   - Reference jira_credentials_secret_arn in IAM policy Resource block
#   - Reference webhook_secret_arn in IAM policy Resource block
#   - Reference mongodb_connection_string_secret_arn conditionally if enable_mongo=true
#   - Reference kms_key_arn in IAM policy for kms:Decrypt action
#   - Policy statement example:
#       {
#         "Effect": "Allow",
#         "Action": "secretsmanager:GetSecretValue",
#         "Resource": [module.secrets.jira_credentials_secret_arn]
#       }
#
# ECS Module Integration:
#   - Reference secret ARNs in task definition 'secrets' block with valueFrom
#   - Example:
#       secrets = [
#         {
#           name      = "JIRA_BASE_URL"
#           valueFrom = "${module.secrets.jira_credentials_secret_arn}:base_url::"
#         }
#       ]
#
# Application Runtime:
#   - Use secret names for boto3 secretsmanager.get_secret_value() calls
#   - Example: secrets_manager.get_secret_value(SecretId=secret_name)
# ------------------------------------------------------------------------------
