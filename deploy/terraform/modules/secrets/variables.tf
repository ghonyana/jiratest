# Terraform Variables for Secrets Module
# Error Triage → Jira Upserter Service
# AWS Secrets Manager configuration for credential and secret management

variable "environment" {
  type        = string
  description = "Deployment environment name (dev, staging, production). Used for environment-specific secret naming convention: jira/jiratest/{environment}/credentials. Determines secret recovery window defaults and resource isolation strategies."

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: dev, staging, production."
  }
}

variable "project" {
  type        = string
  default     = "jiratest"
  description = "Project identifier for consistent resource naming across AWS infrastructure. Used in secret naming pattern: {project}-{environment}-{resource-type} and resource tagging. Aligns with technical specification naming convention from Section 0.2.1."
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region for Secrets Manager and KMS resource deployment. Default us-east-1 matches technical specification deployment region from Section 8.2.1. Must match ECS service deployment region for minimum latency secret retrieval."
}

variable "enable_mongo" {
  type        = bool
  default     = false
  description = "Enable creation of MongoDB Atlas connection string secret. Default false indicates optional MongoDB audit logging feature (per Section 0.1.1 'OPTIONAL for v1'). When true, creates mongodb/jiratest/{environment}/connection-string secret for pymongo client configuration."
}

variable "recovery_window_in_days" {
  type        = number
  default     = 30
  description = "Number of days to retain deleted secrets before permanent deletion. Production environments use default 30 days for compliance and rollback requirements. Development/staging can override to 7 days for faster cleanup. Valid range: 7-30 days per AWS Secrets Manager constraints."

  validation {
    condition     = var.recovery_window_in_days >= 7 && var.recovery_window_in_days <= 30
    error_message = "Recovery window must be between 7 and 30 days (AWS Secrets Manager constraint)."
  }
}

variable "tags" {
  type        = map(string)
  description = "Resource tags for cost allocation, compliance, and operational tracking. Applied to all Secrets Manager secrets and KMS keys. Required tags: Environment, Service, Project, ManagedBy. Used for CloudWatch cost analysis and AWS Config compliance rules."

  default = {
    Service   = "error-triage"
    ManagedBy = "terraform"
  }
}

variable "kms_key_rotation_enabled" {
  type        = bool
  default     = true
  description = "Enable automatic annual rotation for KMS customer managed key encrypting secrets. Default true implements security best practice from Section 8.3.2.3. Rotation occurs automatically every 365 days without service disruption. Disable only for testing or specific compliance requirements."
}

variable "secret_version_count" {
  type        = number
  default     = 30
  description = "Number of secret versions to retain in version history. Default 30 provides audit trail for credential rotation and incident investigation per Section 8.3.2.3. Older versions automatically pruned after limit reached. Minimum 10 recommended for compliance."

  validation {
    condition     = var.secret_version_count >= 10 && var.secret_version_count <= 100
    error_message = "Secret version count must be between 10 and 100 for practical audit trail management."
  }
}
