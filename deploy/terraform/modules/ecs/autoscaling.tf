# =============================================================================
# ECS Service Auto Scaling Configuration
# =============================================================================
# This file defines auto-scaling resources for the Error Triage → Jira Upserter
# ECS service to handle dynamic traffic patterns (100 req/s sustained, 500 req/s peak).
#
# Resources:
# - Auto Scaling Target: Registers ECS service with AWS Application Auto Scaling
# - Target Tracking Policy: Scales based on 70% CPU utilization threshold
# - CloudWatch Alarms: Monitors service health and performance metrics
#
# Environment-specific capacity:
# - Production: min 2, max 20 tasks
# - Staging: min 2, max 10 tasks  
# - Development: min 2, max 4 tasks
# =============================================================================

# -----------------------------------------------------------------------------
# Auto Scaling Target
# -----------------------------------------------------------------------------
# Registers the ECS service as a scalable target with Application Auto Scaling.
# This enables dynamic adjustment of the desired task count based on policies.
# -----------------------------------------------------------------------------

resource "aws_appautoscaling_target" "ecs_target" {
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.main.name}"
  
  # Environment-specific capacity limits
  # Production: 2-20 tasks to handle burst traffic (500 req/s peak)
  # Staging: 2-10 tasks for testing scaling behavior
  # Development: 2-4 tasks for minimal resource usage
  min_capacity = var.min_capacity
  max_capacity = var.max_capacity

  tags = {
    Name        = "jiratest-error-triage-autoscaling-target-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# Target Tracking Scaling Policy - CPU Utilization
# -----------------------------------------------------------------------------
# Automatically adjusts task count to maintain 70% average CPU utilization.
# 
# Scaling behavior:
# - Scale out: 60 second cooldown for rapid response to traffic spikes
# - Scale in: 300 second cooldown to prevent thrashing during transient drops
#
# This policy ensures the service can handle sustained 100 req/s workload
# while scaling up to 20 tasks for 500 req/s peak traffic.
# -----------------------------------------------------------------------------

resource "aws_appautoscaling_policy" "cpu_tracking" {
  name               = "jiratest-error-triage-cpu-tracking-${var.environment}"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs_target.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs_target.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    # Target 70% CPU utilization per Section 8.3.2.1
    target_value = var.target_cpu_utilization

    # Scale out quickly (60s) to handle traffic spikes
    # Scale in slowly (300s) to prevent thrashing during transient drops
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    # Disable scale-in during deployments to maintain stability
    disable_scale_in = false
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarm - High CPU Utilization
# -----------------------------------------------------------------------------
# Alerts when CPU utilization exceeds 80% for 2 consecutive periods.
# This indicates the service is approaching capacity limits and may need
# manual intervention if auto-scaling has reached max_capacity.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "jiratest-error-triage-high-cpu-${var.environment}"
  alarm_description   = "ECS service CPU utilization is above 80% - potential under-provisioning. Review max_capacity if alarm persists."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 80.0
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.main.name
  }

  # Optional: Send notifications to SNS topic for on-call alerts
  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Name        = "jiratest-error-triage-high-cpu-alarm-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    Severity    = "warning"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarm - Low CPU Utilization
# -----------------------------------------------------------------------------
# Alerts when CPU utilization is below 30% for 10 consecutive periods.
# This suggests over-provisioning and opportunity for cost optimization
# by reducing min_capacity or adjusting target_cpu_utilization.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "low_cpu" {
  alarm_name          = "jiratest-error-triage-low-cpu-${var.environment}"
  alarm_description   = "ECS service CPU utilization is below 30% for extended period - potential over-provisioning. Consider reducing min_capacity."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 10
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 30.0
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.main.name
  }

  # Optional: Send notifications for cost optimization opportunities
  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Name        = "jiratest-error-triage-low-cpu-alarm-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    Severity    = "info"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarm - Unhealthy Task Count
# -----------------------------------------------------------------------------
# Alerts when ALB target group reports unhealthy hosts (failed /healthz checks).
# This indicates tasks are failing health checks due to:
# - Redis connectivity issues
# - Jira API unavailability
# - MongoDB connection failures (if enabled)
# - Application crashes or hangs
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "unhealthy_tasks" {
  alarm_name          = "jiratest-error-triage-unhealthy-tasks-${var.environment}"
  alarm_description   = "ECS tasks failing /healthz health checks. Check Redis, Jira API, and application logs for root cause."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnhealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = aws_lb_target_group.app.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  # Critical alarm - requires immediate investigation
  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Name        = "jiratest-error-triage-unhealthy-tasks-alarm-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    Severity    = "critical"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarm - High Response Time
# -----------------------------------------------------------------------------
# Alerts when p95 response time exceeds 1 second (1000ms).
# Target is < 200ms p95 per Section 0.1.2, so 1s indicates severe degradation.
# 
# Common causes:
# - Jira API latency or rate limiting
# - Redis connection pool exhaustion
# - Database query performance issues
# - Insufficient task capacity (check if at max_capacity)
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "high_latency" {
  alarm_name          = "jiratest-error-triage-high-latency-${var.environment}"
  alarm_description   = "ALB target response time p95 > 1 second. Service degradation detected. Target is < 200ms p95."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p95"
  threshold           = 1.0  # 1 second in seconds
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = aws_lb_target_group.app.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  # High severity - indicates user-impacting performance degradation
  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Name        = "jiratest-error-triage-high-latency-alarm-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    Severity    = "high"
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarm - High HTTP 5xx Error Rate
# -----------------------------------------------------------------------------
# Alerts when target HTTP 5xx responses exceed 5 errors in a 5-minute window.
# This indicates application errors that may require immediate attention.
#
# Common causes:
# - Unhandled exceptions in Flask application
# - Dependency failures (Redis, Jira, MongoDB)
# - Memory exhaustion or resource limits
# - Configuration errors
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "high_5xx_errors" {
  alarm_name          = "jiratest-error-triage-high-5xx-errors-${var.environment}"
  alarm_description   = "High rate of HTTP 5xx errors from ECS tasks. Check application logs and dependency health."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300  # 5 minutes
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = aws_lb_target_group.app.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  # Critical alarm - application errors affecting webhook processing
  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Name        = "jiratest-error-triage-high-5xx-errors-alarm-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    Severity    = "critical"
    ManagedBy   = "terraform"
  }
}
