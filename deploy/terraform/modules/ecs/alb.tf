#############################################################################
# Application Load Balancer Configuration
# Purpose: HTTPS webhook ingress from Vercel and GCP platforms to ECS tasks
# Module: deploy/terraform/modules/ecs
#
# This Terraform configuration provisions:
# 1. Internet-facing Application Load Balancer in public subnets
# 2. Target group with IP target type for ECS Fargate awsvpc network mode
# 3. HTTPS listener (port 443) with TLS 1.2+ termination using ACM certificate
# 4. HTTP listener (port 80) redirecting to HTTPS with 301 permanent redirect
# 5. Health checks to /healthz endpoint validating dependency connectivity
#
# Required Variables (defined in variables.tf):
# - var.environment: string - Deployment environment (dev, staging, production)
# - var.public_subnet_ids: list(string) - Public subnet IDs for ALB (3 AZs)
# - var.alb_security_group_id: string - Security group ID for ALB ingress rules
# - var.vpc_id: string - VPC ID for target group association
# - var.container_port: number - ECS task container port (default: 8000)
# - var.health_check_path: string - Health check endpoint path (default: "/healthz")
# - var.enable_access_logs: bool - Enable ALB access logs to S3 (optional)
# - var.access_logs_bucket: string - S3 bucket for access logs (if enabled)
#
# Outputs:
# - alb_dns_name: DNS name for Route53 alias records
# - alb_arn: ARN for monitoring and policies
# - alb_zone_id: Route53 zone ID for alias records
# - target_group_arn: ARN for ECS service load balancer integration
# - target_group_name: Name for reference and monitoring
# - https_listener_arn: ARN for adding custom listener rules
#############################################################################

#############################################################################
# Application Load Balancer
# Internet-facing ALB for external webhook traffic with TLS termination
#############################################################################
resource "aws_lb" "main" {
  name               = "jiratest-error-triage-alb-${var.environment}"
  load_balancer_type = "application"
  internal           = false # Internet-facing for external webhook traffic
  subnets            = var.public_subnet_ids

  security_groups = [var.alb_security_group_id]

  # Enable cross-zone load balancing for even distribution across 3 AZs
  enable_cross_zone_load_balancing = true

  # Deletion protection enabled for production to prevent accidental deletion
  enable_deletion_protection = var.environment == "production" ? true : false

  # Idle timeout for webhook request handling (60 seconds)
  idle_timeout = 60

  # Enable access logs for security auditing and troubleshooting
  # Logs stored in S3 bucket for long-term retention
  dynamic "access_logs" {
    for_each = var.enable_access_logs ? [1] : []
    content {
      bucket  = var.access_logs_bucket
      prefix  = "alb/${var.environment}"
      enabled = true
    }
  }

  tags = {
    Name        = "jiratest-error-triage-alb-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
    Component   = "load-balancer"
  }
}

#############################################################################
# Target Group
# IP-based target group for ECS Fargate tasks using awsvpc network mode
#############################################################################
resource "aws_lb_target_group" "app" {
  name        = "jiratest-error-triage-tg-${var.environment}"
  port        = var.container_port # Port 8000: Flask application with Gunicorn
  protocol    = "HTTP"             # TLS terminated at ALB layer
  vpc_id      = var.vpc_id
  target_type = "ip" # Required for ECS awsvpc network mode

  # Connection draining timeout for graceful task shutdown during deployments
  deregistration_delay = 30

  # Health check configuration validating dependency connectivity
  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.health_check_path # "/healthz"
    port                = "traffic-port"        # Same as target group port
    healthy_threshold   = 2                     # 2 consecutive successes
    unhealthy_threshold = 2                     # 2 consecutive failures
    timeout             = 5                     # 5-second timeout
    interval            = 30                    # 30-second interval
    matcher             = "200"                 # HTTP 200 OK status

  }

  # Sticky sessions disabled for stateless service design
  stickiness {
    type    = "lb_cookie"
    enabled = false
  }

  # Lifecycle management for zero-downtime target group updates
  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "jiratest-error-triage-tg-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
    Component   = "target-group"
  }
}

#############################################################################
# ACM Certificate Lookup
# Wildcard certificate for *.jiratest.com domain with automatic renewal
#############################################################################
data "aws_acm_certificate" "wildcard" {
  domain      = "*.jiratest.com"
  statuses    = ["ISSUED"]
  most_recent = true
}

#############################################################################
# HTTPS Listener (Port 443)
# TLS 1.2+ termination with ACM certificate, forwards to target group
#############################################################################
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"

  # TLS 1.2+ security policy enforcing strong cipher suites
  ssl_policy = "ELBSecurityPolicy-TLS-1-2-2017-01"

  # ACM wildcard certificate for *.jiratest.com
  certificate_arn = data.aws_acm_certificate.wildcard.arn

  # Default action: forward webhook requests to ECS tasks
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = {
    Name        = "jiratest-error-triage-https-listener-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
  }
}

#############################################################################
# HTTP Listener (Port 80)
# Redirects all HTTP traffic to HTTPS to prevent plaintext transmission
#############################################################################
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # Redirect to HTTPS with 301 permanent redirect
  default_action {
    type = "redirect"

    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301" # Permanent redirect
    }
  }

  tags = {
    Name        = "jiratest-error-triage-http-listener-${var.environment}"
    Environment = var.environment
    Service     = "error-triage"
    ManagedBy   = "terraform"
  }
}

#############################################################################
# Outputs
# ALB DNS name and ARN for Route53 alias records and monitoring
#############################################################################
output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer for Route53 alias record"
  value       = aws_lb.main.dns_name
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer for monitoring and policies"
  value       = aws_lb.main.arn
}

output "alb_zone_id" {
  description = "Route53 hosted zone ID of the ALB for alias record creation"
  value       = aws_lb.main.zone_id
}

output "target_group_arn" {
  description = "ARN of the target group for ECS service association"
  value       = aws_lb_target_group.app.arn
}

output "target_group_name" {
  description = "Name of the target group for reference and monitoring"
  value       = aws_lb_target_group.app.name
}

output "https_listener_arn" {
  description = "ARN of the HTTPS listener for adding listener rules"
  value       = aws_lb_listener.https.arn
}
