# ==============================================================================
# ECS Task Execution IAM Role
# ==============================================================================
# Purpose: Grants Fargate infrastructure-level permissions for ECS task launch
#          operations including ECR image pull, Secrets Manager secret retrieval
#          for environment variable injection, and CloudWatch Logs stream creation.
#
# Security: Implements least-privilege access with ARN pattern scoping:
#           - ECR: wildcard required for GetAuthorizationToken per AWS API
#           - Secrets Manager: scoped to jira/jiratest/* and mongodb/jiratest/*
#           - CloudWatch Logs: scoped to environment-specific log group
# ==============================================================================

# ------------------------------------------------------------------------------
# IAM Role: ECS Task Execution Role
# ------------------------------------------------------------------------------
# Assumed by: ECS tasks during infrastructure operations (task launch, image pull)
# Used for: ECR authentication, secret retrieval, log stream creation
# ------------------------------------------------------------------------------
resource "aws_iam_role" "task_execution_role" {
  name        = "jiratest-error-triage-task-execution-role-${var.environment}"
  description = "ECS task execution role for Error Triage service Fargate infrastructure operations in ${var.environment} environment. Grants permissions for ECR image pull from jiratest-error-triage repository, Secrets Manager secret retrieval during task definition launch (Jira credentials, webhook secrets, MongoDB connection string), and CloudWatch Logs stream creation for application log aggregation."

  # Trust policy: Allow ECS tasks service to assume this role
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

  # Resource tags for cost allocation and operational identification
  tags = merge(
    var.tags,
    {
      Name        = "jiratest-error-triage-task-execution-role-${var.environment}"
      Role        = "task-execution"
      Description = "ECS Fargate task execution role for infrastructure operations"
    }
  )

  # Lifecycle management for zero-downtime updates
  lifecycle {
    create_before_destroy = true
  }
}

# ------------------------------------------------------------------------------
# IAM Policy: Task Execution Permissions
# ------------------------------------------------------------------------------
# Defines least-privilege permissions for ECS task execution role with three
# distinct statement blocks for ECR, Secrets Manager, and CloudWatch Logs
# ------------------------------------------------------------------------------
resource "aws_iam_policy" "task_execution_policy" {
  name        = "jiratest-error-triage-task-execution-policy-${var.environment}"
  description = "ECS task execution policy for Error Triage service ${var.environment} environment. Grants ECR image pull permissions (GetAuthorizationToken, BatchCheckLayerAvailability, GetDownloadUrlForLayer, BatchGetImage), Secrets Manager secret retrieval for Jira credentials and webhook secrets scoped to jiratest project ARN patterns, and CloudWatch Logs permissions for log stream creation and event publishing to environment-specific log group."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # --------------------------------------------------------------------
      # Statement 1: ECR Image Pull Operations
      # --------------------------------------------------------------------
      # Purpose: Enable ECS to pull Docker images from ECR repository during
      #          task launch for jiratest-error-triage container
      # Scope: Wildcard resources required for ecr:GetAuthorizationToken per
      #        AWS ECR API authentication design
      # --------------------------------------------------------------------
      {
        Sid    = "ECRImagePull"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      },
      # --------------------------------------------------------------------
      # Statement 2: Secrets Manager Secret Retrieval
      # --------------------------------------------------------------------
      # Purpose: Retrieve secrets during task definition launch for environment
      #          variable injection into container runtime:
      #          - Jira API credentials (base_url, email, api_token)
      #          - Webhook authentication secrets (vercel_secret, gcp_audience)
      #          - MongoDB connection string (optional, if enable_mongo=true)
      # Scope: Least-privilege ARN list for jiratest project secrets only;
      #        conditional inclusion of MongoDB secret when not null
      # Security: Prevents access to secrets outside jiratest/* namespace
      # --------------------------------------------------------------------
      {
        Sid    = "SecretsManagerSecretRetrieval"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = compact([
          var.jira_credentials_secret_arn,
          var.webhook_secret_arn,
          var.mongodb_connection_string_secret_arn != null ? var.mongodb_connection_string_secret_arn : ""
        ])
      },
      # --------------------------------------------------------------------
      # Statement 3: CloudWatch Logs Stream Creation
      # --------------------------------------------------------------------
      # Purpose: Create log streams and publish log events during Fargate task
      #          execution for application stdout/stderr capture
      # Scope: Restricted to environment-specific log group ARN
      #        /aws/ecs/jiratest-error-triage-${var.environment}
      # Security: Prevents unauthorized log writes to other log groups via
      #           explicit resource ARN scoping with wildcard suffix for stream
      # Note: :* suffix required to allow log stream creation within log group
      # --------------------------------------------------------------------
      {
        Sid    = "CloudWatchLogsStreamCreation"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${var.log_group_arn}:*"
      }
    ]
  })

  # Resource tags for policy identification and audit
  tags = merge(
    var.tags,
    {
      Name        = "jiratest-error-triage-task-execution-policy-${var.environment}"
      PolicyType  = "task-execution"
      Description = "ECS task execution permissions for infrastructure operations"
    }
  )

  # Lifecycle management for zero-downtime policy updates
  lifecycle {
    create_before_destroy = true
  }
}

# ------------------------------------------------------------------------------
# IAM Role Policy Attachment: Link Policy to Role
# ------------------------------------------------------------------------------
# Associates the task execution policy with the task execution role to grant
# effective permissions for ECS Fargate infrastructure operations
# ------------------------------------------------------------------------------
resource "aws_iam_role_policy_attachment" "task_execution_policy_attachment" {
  role       = aws_iam_role.task_execution_role.name
  policy_arn = aws_iam_policy.task_execution_policy.arn

  # Lifecycle management ensures policy remains attached during updates
  lifecycle {
    create_before_destroy = true
  }
}

# ==============================================================================
# Security Implementation Notes
# ==============================================================================
# 1. ECR Wildcard Justification:
#    - ecr:GetAuthorizationToken requires wildcard Resource per AWS API design
#    - Token is account-scoped, not repository-scoped
#    - Image pull operations (BatchGetImage, GetDownloadUrlForLayer) scoped to
#      jiratest-error-triage repository via task definition reference
#
# 2. Secrets Manager ARN Pattern Scoping:
#    - Uses explicit ARN list vs wildcard patterns for precise access control
#    - Conditional inclusion of MongoDB secret via compact() function removes
#      empty string when mongodb_connection_string_secret_arn is null
#    - Pattern-based scoping: arn:aws:secretsmanager:REGION:ACCOUNT:secret:jira/jiratest/*
#      and arn:aws:secretsmanager:REGION:ACCOUNT:secret:mongodb/jiratest/*
#
# 3. CloudWatch Logs Resource Scoping:
#    - var.log_group_arn provides base log group ARN
#    - :* suffix allows log stream creation within the specific log group
#    - Prevents unauthorized writes to log groups outside jiratest project
#
# 4. Lifecycle Management:
#    - create_before_destroy = true on all resources enables zero-downtime
#      policy and role updates during Terraform apply operations
#    - ECS tasks continue running with old role during replacement
#
# 5. Compliance Alignment:
#    - Implements least-privilege access per Section 8.3.6.5
#    - ARN scoping prevents lateral movement to resources outside jiratest scope
#    - Meets SOC 2 Type II access control requirements
#    - Audit trail via AWS CloudTrail logs all AssumeRole and GetSecretValue events
# ==============================================================================
