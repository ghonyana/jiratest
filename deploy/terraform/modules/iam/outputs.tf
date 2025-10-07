#
# IAM Module Outputs
#
# Exports IAM role identifiers for consumption by ECS module and root module.
# These outputs enable the ECS task definition to reference the appropriate
# execution and runtime roles for Fargate container orchestration.
#

# Task Execution Role ARN
# Used by ECS task definition's execution_role_arn field
output "task_execution_role_arn" {
  description = "ARN of the ECS task execution role for ECS task definition execution_role_arn field. Grants Fargate infrastructure permissions for ECR image pull, Secrets Manager secret retrieval during task launch, and CloudWatch log stream creation per Section 8.3.2.1 and 8.3.6.4"
  value       = aws_iam_role.task_execution_role.arn
}

# Task Execution Role Name
# Used for IAM policy attachment references and operational documentation
output "task_execution_role_name" {
  description = "Name of the ECS task execution role for IAM policy attachment references and CloudWatch dashboard filtering"
  value       = aws_iam_role.task_execution_role.name
}

# Task Runtime Role ARN
# Used by ECS task definition's task_role_arn field
output "task_role_arn" {
  description = "ARN of the ECS task runtime role for ECS task definition task_role_arn field. Grants application container permissions for CloudWatch Logs write operations (logs:PutLogEvents), optional Secrets Manager secret refresh for credential cache updates, and CloudWatch custom metrics publishing (cloudwatch:PutMetricData) to namespace Jiratest/ErrorTriage per Section 8.3.2.4 and 8.3.6.2"
  value       = aws_iam_role.task_role.arn
}

# Task Runtime Role Name
# Used for IAM policy management and audit trail documentation
output "task_role_name" {
  description = "Name of the ECS task runtime role for IAM policy management and audit trail documentation"
  value       = aws_iam_role.task_role.name
}
