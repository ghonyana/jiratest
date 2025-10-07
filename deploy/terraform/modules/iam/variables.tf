# =============================================================================
# IAM Module Input Variables
# =============================================================================
# Purpose: Defines input parameters for IAM role and policy provisioning
#          for the Error Triage → Jira Upserter service ECS tasks
#
# Module: deploy/terraform/modules/iam
# Owner: Platform Engineering Team
# Project: jiratest-error-triage
# =============================================================================

# -----------------------------------------------------------------------------
# Environment Configuration
# -----------------------------------------------------------------------------

variable "environment" {
  description = <<-EOT
    Deployment environment identifier for IAM resource naming and tagging.
    Used to construct IAM role and policy names following pattern:
    jiratest-error-triage-{role-type}-${environment}
    
    Valid values: dev, staging, production
    
    Example usage:
    - dev: Development environment with relaxed policies for testing
    - staging: Pre-production environment mirroring production IAM configuration
    - production: Production environment with strict least-privilege policies
    
    IAM role names generated:
    - Task Execution Role: jiratest-error-triage-task-execution-role-${environment}
    - Task Role: jiratest-error-triage-task-role-${environment}
  EOT
  
  type = string
  
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: dev, staging, production. Received: ${var.environment}"
  }
}

variable "project" {
  description = <<-EOT
    Project identifier for consistent resource naming and tagging across IAM resources.
    Used in resource name prefixes and cost allocation tags to identify resources
    belonging to the Error Triage service within multi-project AWS accounts.
    
    Default: "jiratest"
    
    Example resource naming pattern:
    - IAM Role: ${project}-error-triage-task-execution-role-${environment}
    - IAM Policy: ${project}-error-triage-task-policy-${environment}
    
    Referenced in:
    - IAM role and policy resource names
    - Resource tags for cost tracking and organizational filtering
    - CloudWatch dashboard grouping and metric namespaces
  EOT
  
  type    = string
  default = "jiratest"
}

# -----------------------------------------------------------------------------
# AWS Region Configuration
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = <<-EOT
    AWS region for IAM resources and ARN pattern construction.
    While IAM is a global service, region specification is required for:
    - Secrets Manager ARN pattern scoping (arn:aws:secretsmanager:${region}:*:secret:...)
    - CloudWatch Logs ARN references (arn:aws:logs:${region}:*:log-group:...)
    - Regional service endpoint resolution for AWS SDK operations
    
    Default: "us-east-1" (primary deployment region per technical specification)
    
    Region consistency requirements:
    - Must match region of ElastiCache Redis cluster for VPC connectivity
    - Must match region of ECS Fargate service deployment
    - Must match region of Secrets Manager secrets and CloudWatch log groups
    
    Reference: Technical specification Section 8.3.2 (AWS region: us-east-1)
  EOT
  
  type    = string
  default = "us-east-1"
}

# -----------------------------------------------------------------------------
# Secrets Manager ARNs for IAM Policy Scoping
# -----------------------------------------------------------------------------

variable "jira_credentials_secret_arn" {
  description = <<-EOT
    ARN of the AWS Secrets Manager secret containing Jira Cloud API credentials
    (base URL, email, API token) required for Jira REST API operations.
    
    Expected secret structure (JSON):
    {
      "base_url": "https://<organization>.atlassian.net",
      "email": "api-user@example.com",
      "api_token": "<jira-api-token>"
    }
    
    IAM policy grants:
    - Task Execution Role: secretsmanager:GetSecretValue at task launch for environment
      variable injection into ECS container runtime
    - Task Role: secretsmanager:GetSecretValue for on-demand credential cache refresh
      (1-hour TTL cache per Section 8.3.6.2)
    
    Secret naming convention:
    - Pattern: jira/jiratest/${environment}/credentials
    - Example: arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/production/credentials-AbCdEf
    
    Security requirements:
    - KMS encryption with customer managed key (CMK)
    - Manual rotation every 90 days per security policy
    - CloudTrail logging for all GetSecretValue operations
    
    Reference: Technical specification Section 8.3.2.3 (Secrets Manager configuration)
  EOT
  
  type = string
}

variable "webhook_secret_arn" {
  description = <<-EOT
    ARN of the AWS Secrets Manager secret containing webhook authentication secrets
    for validating inbound requests from Vercel Log Drain and GCP Pub/Sub push subscriptions.
    
    Expected secret structure (JSON):
    {
      "vercel_secret": "<hmac-shared-secret>",
      "gcp_audience": "https://error-triage.jiratest.com"
    }
    
    IAM policy grants:
    - Task Execution Role: secretsmanager:GetSecretValue at task launch
    - Task Role: secretsmanager:GetSecretValue for on-demand secret refresh
      (5-minute TTL cache for webhook signature verification per Section 8.3.6.2)
    
    Secret usage:
    - Vercel webhook: HMAC-SHA256 signature validation using x-vercel-signature header
    - GCP Pub/Sub: OIDC JWT Bearer token audience claim verification
    
    Secret naming convention:
    - Pattern: jira/jiratest/${environment}/webhook-secret
    - Example: arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/production/webhook-secret-XyZaBc
    
    Security requirements:
    - Manual rotation every 180 days
    - Vercel webhook configuration must be updated synchronously with rotation
    - Never logged in CloudWatch Logs or error messages
    
    Reference: Technical specification Section 8.3.6.2 (Webhook authentication)
  EOT
  
  type = string
}

variable "mongodb_connection_string_secret_arn" {
  description = <<-EOT
    (Optional) ARN of the AWS Secrets Manager secret containing MongoDB Atlas connection
    string for audit trail persistence (error_events and jira_actions collections).
    
    Expected secret structure (plain text):
    mongodb+srv://<username>:<password>@<cluster>.mongodb.net/<database>?retryWrites=true&w=majority
    
    IAM policy grants:
    - Task Execution Role: secretsmanager:GetSecretValue at task launch for MongoDB client
      initialization during application startup
    - Task Role: Optional secretsmanager:GetSecretValue for connection string refresh
      (long-lived connection pool does not require frequent secret retrieval)
    
    Optional configuration:
    - Set to null to disable MongoDB integration (audit logging disabled)
    - Application gracefully degrades when MongoDB unavailable; audit trail recoverable
      from CloudWatch structured JSON logs
    - Required only when ENABLE_MONGO=true environment variable configured
    
    Secret naming convention:
    - Pattern: mongodb/jiratest/${environment}/connection-string
    - Example: arn:aws:secretsmanager:us-east-1:123456789012:secret:mongodb/jiratest/production/connection-string-MnOpQr
    
    Security requirements:
    - Automatic rotation every 90 days via Lambda rotation function
    - MongoDB Atlas user password updated synchronously via admin API
    - Connection string contains sensitive credentials; never logged
    
    Default: null (MongoDB integration disabled; application operates without audit persistence)
    
    Reference: Technical specification Section 8.3.2.3 (Optional MongoDB configuration)
  EOT
  
  type    = string
  default = null
}

# -----------------------------------------------------------------------------
# CloudWatch Logs Configuration
# -----------------------------------------------------------------------------

variable "log_group_arn" {
  description = <<-EOT
    ARN of the CloudWatch Logs log group for ECS task structured JSON log streaming.
    
    IAM policy grants:
    - Task Execution Role: logs:CreateLogStream, logs:PutLogEvents for log stream
      initialization during Fargate task launch
    - Task Role: logs:PutLogEvents for application runtime log emission via Python
      logging module with JSON formatter
    
    Log group naming convention:
    - Pattern: /aws/ecs/jiratest-error-triage-${environment}
    - Example: arn:aws:logs:us-east-1:123456789012:log-group:/aws/ecs/jiratest-error-triage-production
    
    Log stream pattern:
    - ECS Fargate automatically creates streams: error-triage/${task_id}
    - One log stream per ECS task for task-level log isolation
    
    Log format requirements:
    - Structured JSON with fields: timestamp, level, service, environment, event_id,
      fingerprint, jira_issue_key, source, action, duration_ms, success
    - Compatible with CloudWatch Logs Insights queries for distributed tracing
    - PII sanitization applied before log emission (no emails, UUIDs, tokens in logs)
    
    Retention policy:
    - Application logs: 90 days per Section 8.3.2.4
    - Authentication logs: 90 days (security events)
    - S3 archive for 7-year compliance retention via CloudWatch Logs export
    
    IAM policy scoping:
    - Restricted to specific log group ARN to prevent unauthorized log access
    - Suffix :* permits writing to any log stream within the log group
    
    Reference: Technical specification Section 8.3.2.4 (CloudWatch Logs configuration)
  EOT
  
  type = string
}

# -----------------------------------------------------------------------------
# Resource Tagging
# -----------------------------------------------------------------------------

variable "tags" {
  description = <<-EOT
    Resource tags applied to all IAM roles and policies for organizational tracking,
    cost allocation, and operational filtering.
    
    Required tag keys (per Section 8.3.5 cost allocation strategy):
    - Environment: Deployment environment (dev/staging/production)
    - Service: Service component identifier (error-triage, iam-roles)
    - Project: Project identifier for multi-project AWS accounts (jiratest-error-triage)
    - ManagedBy: Infrastructure management tool (terraform)
    - Team: Owning team for operational responsibility (platform-engineering)
    - CostCenter: Financial chargeback allocation (infrastructure/product-development)
    
    Tag usage:
    - AWS Cost Explorer filtering for environment-specific budget tracking
    - IAM role identification in CloudWatch dashboards and alarms
    - Terraform resource lifecycle management (prevent accidental deletion)
    - AWS Organizations tag-based access control policies
    
    Example tag map:
    {
      Environment = "production"
      Service     = "error-triage-iam"
      Project     = "jiratest-error-triage"
      ManagedBy   = "terraform"
      Team        = "platform-engineering"
      CostCenter  = "infrastructure"
    }
    
    Tag propagation:
    - Tags applied to aws_iam_role.task_execution_role
    - Tags applied to aws_iam_role.task_role
    - IAM policies do not support tags (not applicable per AWS IAM limitations)
    
    Reference: Technical specification Section 8.3.5 (Cost optimization tagging strategy)
  EOT
  
  type = map(string)
}
