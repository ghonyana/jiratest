"""
Flask Route Blueprints Package for Error Triage Service

This package initializes and exports all Flask blueprints for the Error Triage service,
enabling clean modular route registration in the application factory. The package follows
Python best practices with explicit __all__ declarations and relative imports.

Per Section 0.5.1 (File-by-File Execution Plan), this initialization module serves as the
entry point for the routes package, providing three distinct blueprints that handle
different operational concerns:

Contained Blueprints:

1. events_bp (POST /events):
   - Core webhook endpoint for multi-source error ingestion
   - Accepts error events from Vercel Log Drain and GCP Cloud Logging
   - Performs authentication via HMAC signature or OIDC token validation
   - Implements complete processing pipeline: normalize → dedup → fingerprint → track → classify → Jira
   - Returns 202 Accepted within <200ms p95 SLO per Section 0.7.3
   - Emits structured logs and Prometheus metrics for observability
   
2. health_bp (GET /healthz):
   - Health check endpoint for ALB target health determination
   - Validates connectivity to Redis, MongoDB (optional), and Jira dependencies
   - Returns per-dependency latency measurements for operational insight
   - Returns 200 OK when required dependencies (Redis, Jira) are UP
   - Returns 503 Service Unavailable when any required dependency fails
   - Enables ECS task health monitoring and automatic replacement
   
3. metrics_bp (GET /metrics):
   - Prometheus metrics exposition endpoint for observability
   - Exposes text exposition format (version 0.0.4) with charset=utf-8
   - Includes counters: events_received, jira_created, errors_total
   - Includes histograms: event_processing_duration, jira_api_latency
   - Supports multi-process aggregation for Gunicorn worker deployments
   - Scraped by Prometheus or CloudWatch Agent with Prometheus integration

Package Architecture:

The routes package implements the Controller layer in the MVC-inspired architecture:
- Routes handle HTTP request/response concerns (validation, status codes, headers)
- Service layer (src/services/) implements business logic and external integrations
- Models (src/models/) define data structures and validation rules
- Utilities (src/utils/) provide cross-cutting concerns (logging, metrics, auth)

This separation enables:
- Independent testing of route handlers with mocked services
- Service reusability across multiple routes or background jobs
- Clear contract boundaries via dependency injection
- Simplified reasoning about request flow and error handling

Usage Example in Application Factory:

    # In src/app/__init__.py application factory:
    from flask import Flask
    from app.routes import events_bp, health_bp, metrics_bp
    
    def create_app(config_name='production'):
        app = Flask(__name__)
        app.config.from_object(Config)
        
        # Initialize dependencies (Redis, MongoDB, Jira clients)
        redis_client = init_redis(app.config)
        mongo_client = init_mongodb(app.config)
        jira_client = init_jira(app.config)
        
        # Inject dependencies into Flask application context
        @app.before_request
        def inject_dependencies():
            g.redis_client = redis_client
            g.mongo_client = mongo_client
            g.jira_client = jira_client
        
        # Register blueprints with application
        app.register_blueprint(events_bp)
        app.register_blueprint(health_bp)
        app.register_blueprint(metrics_bp)
        
        return app

Blueprint Registration Pattern:

Flask blueprints are registered without URL prefixes, making endpoints available at:
- POST http://error-triage.jiratest.com/events
- GET http://error-triage.jiratest.com/healthz
- GET http://error-triage.jiratest.com/metrics

This flat URL structure aligns with microservice API design best practices and
simplifies ALB health check configuration and Prometheus scraping.

Blueprint Lifecycle:

1. Blueprint Definition: Each route module defines Blueprint('name', __name__)
2. Route Registration: @blueprint.route('/path') decorators attach handlers
3. Package Export: This __init__.py imports and re-exports blueprints
4. App Registration: Application factory calls app.register_blueprint(bp)
5. Request Handling: Flask routes requests to appropriate blueprint handlers

Dependency Injection Pattern:

Blueprints access injected dependencies via Flask's application context (g object):
- g.redis_client: Redis client for frequency tracking and deduplication
- g.mongo_client: MongoDB client for audit logs (None if ENABLE_MONGO=false)
- g.jira_client: Jira API client for issue creation and updates

The events blueprint additionally uses module-level service injection via init_services()
function for complex processing pipeline dependencies.

Security Considerations:

- /events endpoint: REQUIRES webhook authentication (Vercel signature or GCP OIDC)
- /healthz endpoint: NO authentication (designed for ALB health probes, VPC-internal)
- /metrics endpoint: NO authentication (restricted by security groups to monitoring infra)

Per Section 0.7.4, only the /events endpoint implements authentication to prevent
unauthorized webhook submissions. Health and metrics endpoints rely on network-level
security (VPC isolation, security groups) per Section 6.4 (Security Architecture).

Technical References:
- Section 0.2.1: Core Application Files (routes package structure)
- Section 0.5.1: File-by-File Execution Plan (blueprint implementation details)
- Section 3.3.1: Flask Framework (Blueprint pattern and application factory)
- Section 6.5: Monitoring and Observability (health checks and metrics)
- Section 8.2: Deployment Environment (ALB configuration, ECS task health)

Version History:
- 1.0.0: Initial implementation with three core blueprints

Author: Blitzy Platform
Version: 1.0.0
"""

# =============================================================================
# Blueprint Imports from Route Modules
# =============================================================================
# 
# Import Flask Blueprint objects from individual route modules using relative
# imports per PEP 8 import organization. Each blueprint handles a distinct
# operational concern with minimal coupling between blueprints.

from .events import events_bp
from .health import health_bp
from .metrics import metrics_bp


# =============================================================================
# Package Metadata
# =============================================================================

# Package version for tracking across deployments
# Updated on breaking changes to route contracts or blueprint structure
__version__ = "1.0.0"


# =============================================================================
# Public API Definition
# =============================================================================
#
# Explicit declaration of exported symbols for package consumers.
# Controls behavior of 'from app.routes import *' statements.
# Per PEP 8, __all__ provides self-documenting public interface.

__all__ = [
    "events_bp",    # POST /events webhook endpoint blueprint
    "health_bp",    # GET /healthz health check endpoint blueprint
    "metrics_bp",   # GET /metrics Prometheus endpoint blueprint
]
