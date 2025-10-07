"""
Utilities Package for Error Triage Service

This package provides cross-cutting infrastructure concerns and utility functions
for the Error Triage → Jira Upserter microservice. All modules in this package
are designed for reuse across the application's service layer, route handlers,
and initialization code.

Package Contents:
    - logging_config: Structured JSON logging with CloudWatch-compatible format
    - metrics_collector: Prometheus metrics registry and collectors for observability
    - secrets_manager: AWS Secrets Manager integration for secure credential management
    - auth: Webhook authentication for Vercel and GCP Pub/Sub push subscriptions

Key Features:
    - Structured Logging: CloudWatch-compatible JSON logs with mandatory correlation fields
      (event_id, fingerprint, jira_issue_key) for operational troubleshooting and request tracing
    
    - Prometheus Metrics: Counter and histogram metrics for events, Jira operations, and latencies
      exposed via /metrics endpoint for scraping and integration with monitoring infrastructure
    
    - Secrets Management: Secure retrieval of Jira API tokens, webhook secrets, and MongoDB
      connection strings from AWS Secrets Manager with 1-hour in-memory caching and TTL
    
    - Webhook Authentication: HMAC-SHA256 signature verification for Vercel webhooks and
      OIDC JWT token validation for GCP Pub/Sub push subscriptions with timing-attack resistance

Per Section 0.5.1 Group 8 requirements, this package consolidates utility functions
to avoid duplication and establish a single source of truth for infrastructure concerns.

Usage Examples:

    # Structured Logging
    from utils import setup_logging
    setup_logging(level='INFO', environment='production')
    
    # Prometheus Metrics
    from utils import MetricsCollector
    collector = MetricsCollector()
    collector.increment_counter('events_received_total', {'environment': 'prod', 'source': 'vercel'})
    
    # Secrets Management
    from utils import get_secret, SecretsManagerClient
    jira_creds = get_secret('jira/jiratest/production/credentials')
    
    # Webhook Authentication
    from utils import WebhookAuthenticator, verify_vercel_signature, verify_gcp_token
    authenticator = WebhookAuthenticator(vercel_secret=webhook_secret, gcp_audience=audience)
    if not authenticator.verify(request):
        return jsonify({'error': 'Unauthorized'}), 401

Application Initialization (per Section 0.5.1):
    In src/app/__init__.py create_app() factory:
    1. Call setup_logging() to initialize structured JSON logging
    2. Load secrets from AWS Secrets Manager using get_secret()
    3. Initialize MetricsCollector singleton for /metrics endpoint
    4. Create WebhookAuthenticator with loaded secrets for /events endpoint

Security Considerations (per Section 0.7.4):
    - All utilities follow zero-trust principle: validate inputs, fail securely
    - No sensitive data (secrets, tokens, credentials) is ever logged
    - Thread-safe implementations for multi-worker Gunicorn deployments
    - Constant-time comparisons for cryptographic operations (timing-attack resistance)

Integration Points:
    - AWS CloudWatch: Logs streamed via ECS awslogs driver to /aws/ecs/jiratest-error-triage-{env}
    - AWS Secrets Manager: Credentials loaded from us-east-1 region secrets
    - Prometheus: Metrics exposed in text/plain format via /metrics endpoint
    - Vercel: Webhook signature verification using x-vercel-signature header
    - GCP Pub/Sub: OIDC JWT token validation with audience and issuer verification

Author: Blitzy Platform
Version: 1.0.0
"""

# ============================================================================
# Relative Imports from Utility Modules
# ============================================================================

# Structured logging configuration
from .logging_config import setup_logging

# Prometheus metrics collection and exposition
from .metrics_collector import MetricsCollector

# AWS Secrets Manager integration for secure credential retrieval
from .secrets_manager import get_secret, SecretsManagerClient

# Webhook authentication for Vercel and GCP
from .auth import WebhookAuthenticator, verify_vercel_signature, verify_gcp_token


# ============================================================================
# Package Version
# ============================================================================

__version__ = "1.0.0"


# ============================================================================
# Public API Definition
# ============================================================================

__all__ = [
    # Logging utilities
    "setup_logging",
    
    # Metrics utilities
    "MetricsCollector",
    
    # Secrets management utilities
    "get_secret",
    "SecretsManagerClient",
    
    # Authentication utilities
    "WebhookAuthenticator",
    "verify_vercel_signature",
    "verify_gcp_token",
]
