# ==============================================================================
# ECS Task IAM Role for Application Runtime Operations
# ==============================================================================
# Purpose: Grants the Error Triage service application container permissions for
# CloudWatch Logs write operations, optional Secrets Manager secret refresh for
# credential cache updates, and CloudWatch custom metrics publishing.
#
# Security Model: Least-privilege access following AWS IAM best practices with
# resource ARN scoping to prevent unauthorized access outside jiratest project.
# ==============================================================================

# ------------------------------------------------------------------------------
# IAM Role: ECS Task Runtime Role
# ------------------------------------------------------------------------------
# Assume role policy allows ECS tasks service to assume this role, enabling
# application containers to authenticate to AWS services using IAM credentials.
# This role is specified in the ECS task definition task_role_arn field.
# ------------------------------------------------------------------------------

resource "aws_iam_role" "task_role" {
  name        = "jiratest-error-triage-task-role-${var.environment}"
  description = "ECS task runtime role for Error Triage service application container operations including CloudWatch log streaming, optional secret refresh, and custom metrics publishing."

  # Trust policy allowing ECS tasks service to assume this role
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  # Resource tags for identification and cost allocation
  tags = var.tags
}

# ------------------------------------------------------------------------------
# IAM Policy: Task Runtime Permissions
# ------------------------------------------------------------------------------
# Defines least-privilege permissions for application runtime operations:
# 1. CloudWatch Logs: Write log events to service-specific log group
# 2. Secrets Manager: Retrieve secrets for credential cache refresh
# 3. CloudWatch Metrics: Publish custom metrics to service namespace
# ------------------------------------------------------------------------------

resource "aws_iam_policy" "task_policy" {
  name        = "jiratest-error-triage-task-policy-${var.environment}"
  description = "Runtime permissions for Error Triage ECS task: CloudWatch Logs write, optional Secrets Manager refresh, and custom metrics publishing"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Statement 1: CloudWatch Logs Write Access
      # Allows application to stream structured JSON logs to CloudWatch
      # Resource scoped to environment-specific log group with wildcard log streams
      {
        Sid    = "CloudWatchLogsWrite"
        Effect = "Allow"
        Action = [
          "logs:PutLogEvents"
        ]
        Resource = "${var.log_group_arn}:*"
      },

      # Statement 2: Secrets Manager Secret Retrieval (Optional)
      # Enables on-demand credential cache refresh for:
      # - Jira API credentials (1-hour cache TTL)
      # - Webhook authentication secrets (5-minute cache TTL)
      # - MongoDB connection string (optional, included only if not null)
      # Supports credential rotation without service restart per Section 8.3.6.2
      {
        Sid    = "SecretsManagerRefresh"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        # Conditional resource list: include MongoDB secret ARN only if provided
        Resource = compact([
          var.jira_credentials_secret_arn,
          var.webhook_secret_arn,
          var.mongodb_connection_string_secret_arn
        ])
      },

      # Statement 3: CloudWatch Custom Metrics Publishing
      # Allows application to publish Prometheus-format metrics to CloudWatch
      # Resource must be "*" per AWS API requirements, restricted by condition
      # Condition limits metric publishing to service-specific namespace
      {
        Sid    = "CloudWatchCustomMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "Jiratest/ErrorTriage"
          }
        }
      }
    ]
  })

  tags = var.tags
}

# ------------------------------------------------------------------------------
# IAM Role Policy Attachment
# ------------------------------------------------------------------------------
# Links the task runtime policy to the task role, granting all defined
# permissions to application containers running with this role.
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy_attachment" "task_policy_attachment" {
  role       = aws_iam_role.task_role.name
  policy_arn = aws_iam_policy.task_policy.arn
}
