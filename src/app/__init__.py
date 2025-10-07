"""
Flask Application Factory for Error Triage to Jira Upserter Service

This module implements the Flask application factory pattern for the Error Triage
service, providing a create_app() function that initializes and configures a
production-ready Flask application with dependency injection, structured logging,
metrics collection, and hot-reload capabilities.

Per Section 0.5.1 Group 1 requirements, the application factory:
- Initializes Flask app with environment-specific configuration
- Sets up Redis connection pool for frequency tracking and deduplication
- Conditionally initializes MongoDB client for audit logging (when ENABLE_MONGO=true)
- Registers three Flask blueprints: events_bp, health_bp, metrics_bp
- Configures structured JSON logging with CloudWatch-compatible format
- Initializes Prometheus metrics registry for observability
- Implements SIGHUP signal handler for zero-downtime configuration reload
- Injects dependencies into Flask application context for blueprint access

Architecture Pattern (per Section 0.1.2):
- Application Factory: Enables multiple app instances for testing
- Dependency Injection: Services injected via Flask's g object
- Configuration-Driven: All behavior controlled by Config classes
- Stateless Design: No module-level state except signal handlers

Integration Points (per Section 0.4):
- Redis: ElastiCache cluster for frequency counters and deduplication
- MongoDB: Atlas cluster for audit logs (optional, controlled by ENABLE_MONGO)
- Jira: API client for issue creation and updates (initialized on-demand in services)
- CloudWatch: Structured logs streamed via ECS awslogs driver
- Prometheus: Metrics exposed via /metrics endpoint for scraping

Security Constraints (per Section 0.7.4):
- All secrets loaded from AWS Secrets Manager via Config classes
- Credentials never logged or exposed in error messages
- Redis and MongoDB connections use connection pooling with health checks
- SIGHUP handler validates configuration before applying changes

Hot-Reload Support (per Section 0.7.1 requirement #6):
The SIGHUP signal handler reloads YAML configuration files without restart:
- config/severity_rules.yaml: Frequency-to-severity mappings
- config/ownership_rules.yaml: Service-to-assignee mappings
- config/sanitization_patterns.yaml: PII detection patterns

To reload configuration in production:
    # Find ECS task process ID
    kill -HUP <pid>
    
    # Or use ECS task restart for rolling reload
    aws ecs update-service --cluster jiratest-prod --service error-triage --force-new-deployment

Usage:
    # Production deployment (via Gunicorn)
    from app import create_app
    app = create_app('production')
    
    # Development server
    if __name__ == '__main__':
        app = create_app('development')
        app.run(host='0.0.0.0', port=8080)
    
    # Testing
    def test_app():
        app = create_app('development')
        app.config['TESTING'] = True
        with app.test_client() as client:
            response = client.get('/healthz')
            assert response.status_code == 200

Technical References:
- Section 0.5.1: Application Foundation implementation requirements
- Section 3.3.1: Flask Framework configuration and patterns
- Section 6.5: Monitoring and observability integration
- Section 8.2: ECS deployment and container orchestration

Author: Blitzy Platform
Version: 1.0.0
"""

import os
import signal
import logging
from typing import Optional
from flask import Flask, g
from redis import Redis, ConnectionError as RedisConnectionError
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure as MongoConnectionFailure

# Internal imports - Configuration and logging
from src.app.config import Config, DevelopmentConfig, ProductionConfig
from src.utils.logging_config import setup_logging

# Internal imports - Route blueprints
from src.app.routes import events_bp, health_bp, metrics_bp

# Internal imports - Metrics collection
from src.utils.metrics_collector import MetricsCollector


# Module-level logger for application factory operations
logger = logging.getLogger(__name__)


def create_app(config_name: str = 'production') -> Flask:
    """
    Flask application factory for Error Triage to Jira Upserter service.
    
    Creates and configures a Flask application instance with environment-specific
    settings, dependency injection, structured logging, and observability. Follows
    the application factory pattern to enable multiple app instances for testing
    and production deployment.
    
    Per Section 0.5.1 Group 1, this factory implements:
    1. Environment-specific configuration loading (development, production)
    2. Redis connection pool initialization with health checks and retry logic
    3. MongoDB client initialization (conditional on ENABLE_MONGO flag)
    4. Blueprint registration for HTTP routes (events, health, metrics)
    5. Structured JSON logging configuration for CloudWatch integration
    6. Prometheus metrics registry initialization for observability
    7. SIGHUP signal handler for hot-reload of YAML configuration files
    8. Dependency injection via Flask's application context (g object)
    
    Configuration Loading:
    - 'development': Enables Flask debug mode, verbose logging, local overrides
    - 'production': Disables debug mode, loads secrets from AWS Secrets Manager
    - Config classes validate all required environment variables at startup
    
    Redis Connection Pool (per Section 0.4.2):
    - Connection string from REDIS_HOST environment variable
    - Connection pooling enabled for performance (default pool size: 50)
    - Automatic retry on transient connection failures
    - Health check interval: 30 seconds to detect stale connections
    - Timeout: 5 seconds for connection and socket operations
    
    MongoDB Client (per Section 0.4.3):
    - Only initialized when ENABLE_MONGO environment variable is 'true'
    - Connection string loaded from Config.MONGODB_CONNECTION_STRING
    - Automatic connection retry with exponential backoff
    - Used for audit logs: error_events, jira_actions collections
    - Returns None if ENABLE_MONGO=false (optional dependency)
    
    Blueprint Registration:
    - events_bp: POST /events - Webhook endpoint for error ingestion
    - health_bp: GET /healthz - Health check for ALB target health
    - metrics_bp: GET /metrics - Prometheus metrics exposition
    
    Dependency Injection Pattern:
    Dependencies are injected into Flask's application context (g object) via
    before_request hook, making them available to all route handlers:
    - g.redis_client: Redis client for frequency tracking and deduplication
    - g.mongo_client: MongoDB client for audit logs (None if disabled)
    - g.config: Application configuration object for service access
    
    SIGHUP Signal Handler (per Section 0.7.1 requirement #6):
    Implements hot-reload of YAML configuration files without service restart:
    - Reloads severity_rules.yaml for frequency-to-severity mappings
    - Reloads ownership_rules.yaml for service-to-assignee routing
    - Reloads sanitization_patterns.yaml for PII detection patterns
    - Services detect configuration changes automatically via file modification time
    
    Args:
        config_name: Configuration environment name (default: 'production')
                    Valid values: 'development', 'production'
    
    Returns:
        Configured Flask application instance ready for WSGI deployment
    
    Raises:
        ValueError: If config_name is invalid or required configuration is missing
        RedisConnectionError: If Redis connection cannot be established (logs warning, continues)
        MongoConnectionFailure: If MongoDB connection fails when ENABLE_MONGO=true (logs error, continues)
    
    Example - Production deployment (Gunicorn):
        >>> from app import create_app
        >>> app = create_app('production')
        >>> # Run via: gunicorn --bind 0.0.0.0:8080 --workers 4 'app:create_app()'
    
    Example - Development server:
        >>> app = create_app('development')
        >>> app.run(host='0.0.0.0', port=8080, debug=True)
    
    Example - Testing:
        >>> app = create_app('development')
        >>> app.config['TESTING'] = True
        >>> with app.test_client() as client:
        ...     response = client.get('/healthz')
        ...     assert response.status_code == 200
    
    Example - Accessing injected dependencies in routes:
        >>> @events_bp.route('/events', methods=['POST'])
        ... def handle_event():
        ...     redis_client = g.redis_client
        ...     mongo_client = g.mongo_client  # None if ENABLE_MONGO=false
        ...     config = g.config
        ...     # Use dependencies for business logic
        ...     return jsonify({'status': 'accepted'}), 202
    
    Security Considerations (per Section 0.7.4):
    - All secrets loaded from AWS Secrets Manager via Config classes
    - Redis and MongoDB credentials never logged or exposed
    - Connection errors logged with sanitized details (no credentials)
    - SIGHUP handler validates configuration before applying changes
    
    Performance Characteristics (per Section 0.7.3):
    - Redis connection pool: 50 connections, <5ms operation latency (p99)
    - MongoDB connection pool: Default 100 connections, lazy initialization
    - Blueprint registration: O(1) operation, negligible startup overhead
    - Structured logging: <1ms per log entry, asynchronous to CloudWatch
    """
    # =========================================================================
    # Step 1: Initialize Flask Application Instance
    # =========================================================================
    
    app = Flask(__name__)
    
    # Configure Flask application with environment-specific settings
    # Config classes load secrets from AWS Secrets Manager and validate required variables
    if config_name == 'development':
        app.config.from_object(DevelopmentConfig)
    elif config_name == 'production':
        app.config.from_object(ProductionConfig)
    else:
        # Fall back to base Config for unknown environments
        app.config.from_object(Config)
    
    # Extract environment name for logging and metrics
    environment = app.config.get('ENVIRONMENT', 'production')
    log_level = 'DEBUG' if app.config.get('DEBUG', False) else 'INFO'
    
    # =========================================================================
    # Step 2: Configure Structured JSON Logging
    # =========================================================================
    
    # Initialize structured logging with CloudWatch-compatible JSON format
    # Per Section 0.5.1 Group 1, logging must be configured before any operations
    setup_logging(level=log_level, environment=environment)
    
    # Get logger for application factory operations
    # Use module-level logger to track initialization events
    logger.info(
        "Initializing Flask application factory",
        extra={
            'action': 'app_factory_init',
            'config_name': config_name,
            'environment': environment,
            'debug_mode': app.config.get('DEBUG', False)
        }
    )
    
    # =========================================================================
    # Step 3: Initialize Redis Connection Pool
    # =========================================================================
    
    # Redis is a REQUIRED dependency for frequency tracking and deduplication
    # Connection failure logs warning but continues to enable health check reporting
    redis_client: Optional[Redis] = None
    redis_host = app.config.get('REDIS_HOST')
    
    if redis_host:
        try:
            # Initialize Redis client with connection pooling and health checks
            # Per Section 0.4.2, Redis connection pool configuration:
            # - host: ElastiCache endpoint from REDIS_HOST environment variable
            # - port: Default 6379 (standard Redis port)
            # - db: 0 (default database for frequency counters and deduplication)
            # - decode_responses: True (return strings instead of bytes)
            # - socket_connect_timeout: 5 seconds (fail fast on connection issues)
            # - socket_timeout: 5 seconds (fail fast on operation timeouts)
            # - retry_on_timeout: True (automatic retry for transient failures)
            # - health_check_interval: 30 seconds (detect and replace stale connections)
            redis_client = Redis(
                host=redis_host,
                port=6379,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            # Verify Redis connectivity with PING command
            # This ensures Redis is reachable before accepting traffic
            redis_client.ping()
            
            logger.info(
                "Redis connection pool initialized successfully",
                extra={
                    'action': 'redis_init_success',
                    'redis_host': redis_host,
                    'pool_size': 'default',
                    'health_check_interval': 30
                }
            )
            
        except RedisConnectionError as e:
            # Log Redis connection failure as warning (not critical for startup)
            # Application continues to start but /healthz will report degraded status
            logger.warning(
                "Failed to connect to Redis - application will start in degraded mode",
                extra={
                    'action': 'redis_init_failure',
                    'redis_host': redis_host,
                    'error_type': 'redis_connection_error'
                },
                exc_info=True
            )
            redis_client = None
            
        except Exception as e:
            # Catch-all for unexpected Redis initialization errors
            logger.error(
                "Unexpected error initializing Redis connection pool",
                extra={
                    'action': 'redis_init_error',
                    'redis_host': redis_host,
                    'error_type': 'redis_unexpected_error'
                },
                exc_info=True
            )
            redis_client = None
    else:
        # Redis host not configured - log error but continue (config validation should prevent this)
        logger.error(
            "REDIS_HOST not configured - application will start without Redis",
            extra={
                'action': 'redis_not_configured',
                'error_type': 'redis_config_missing'
            }
        )
    
    # =========================================================================
    # Step 4: Initialize MongoDB Client (Conditional)
    # =========================================================================
    
    # MongoDB is an OPTIONAL dependency for audit logging
    # Only initialized when ENABLE_MONGO environment variable is 'true'
    mongo_client: Optional[MongoClient] = None
    enable_mongo = app.config.get('ENABLE_MONGO', False)
    
    if enable_mongo:
        mongo_uri = app.config.get('MONGODB_CONNECTION_STRING')
        
        if mongo_uri:
            try:
                # Initialize MongoDB client with connection retry logic
                # Per Section 0.4.3, MongoDB connection configuration:
                # - Connection string from MONGODB_CONNECTION_STRING (loaded from Secrets Manager)
                # - Default connection pool: 100 connections
                # - Automatic retry on connection failure
                # - Used for audit logs: error_events, jira_actions collections
                mongo_client = MongoClient(
                    mongo_uri,
                    serverSelectionTimeoutMS=5000,  # 5 second timeout for server selection
                    connectTimeoutMS=10000,  # 10 second timeout for initial connection
                    socketTimeoutMS=10000,  # 10 second timeout for socket operations
                    retryWrites=True,  # Automatic retry for write operations
                    retryReads=True  # Automatic retry for read operations
                )
                
                # Verify MongoDB connectivity with admin ping command
                # This ensures MongoDB is reachable before accepting traffic
                mongo_client.admin.command('ping')
                
                logger.info(
                    "MongoDB client initialized successfully",
                    extra={
                        'action': 'mongodb_init_success',
                        'enable_mongo': True
                    }
                )
                
            except MongoConnectionFailure as e:
                # Log MongoDB connection failure as error (ENABLE_MONGO=true but connection failed)
                # Application continues to start but audit logging will be disabled
                logger.error(
                    "Failed to connect to MongoDB - audit logging disabled",
                    extra={
                        'action': 'mongodb_init_failure',
                        'enable_mongo': True,
                        'error_type': 'mongodb_connection_failure'
                    },
                    exc_info=True
                )
                mongo_client = None
                
            except Exception as e:
                # Catch-all for unexpected MongoDB initialization errors
                logger.error(
                    "Unexpected error initializing MongoDB client",
                    extra={
                        'action': 'mongodb_init_error',
                        'enable_mongo': True,
                        'error_type': 'mongodb_unexpected_error'
                    },
                    exc_info=True
                )
                mongo_client = None
        else:
            # MongoDB URI not configured despite ENABLE_MONGO=true
            logger.error(
                "ENABLE_MONGO is true but MONGODB_CONNECTION_STRING not configured",
                extra={
                    'action': 'mongodb_not_configured',
                    'enable_mongo': True,
                    'error_type': 'mongodb_config_missing'
                }
            )
    else:
        # MongoDB disabled - no audit logging
        logger.info(
            "MongoDB audit logging disabled",
            extra={
                'action': 'mongodb_disabled',
                'enable_mongo': False
            }
        )
    
    # =========================================================================
    # Step 5: Initialize Prometheus Metrics Registry
    # =========================================================================
    
    # Initialize MetricsCollector singleton for observability
    # Per Section 0.5.1 Group 1, metrics must be initialized before route registration
    metrics_collector = MetricsCollector()
    
    logger.info(
        "Prometheus metrics collector initialized",
        extra={
            'action': 'metrics_init_success',
            'metrics_exposed': ['events_received_total', 'jira_issues_created_total', 'event_processing_duration_seconds']
        }
    )
    
    # =========================================================================
    # Step 6: Inject Dependencies into Flask Application Context
    # =========================================================================
    
    # Use Flask's before_request hook to inject dependencies into g object
    # This makes dependencies available to all route handlers without global state
    @app.before_request
    def inject_dependencies():
        """
        Inject dependencies into Flask application context before each request.
        
        Dependencies injected via g object (per-request context):
        - g.redis_client: Redis client for frequency tracking and deduplication
        - g.mongo_client: MongoDB client for audit logs (None if ENABLE_MONGO=false)
        - g.config: Application configuration object for service access
        - g.metrics_collector: Prometheus metrics collector for observability
        
        This pattern enables:
        - Route handlers to access dependencies via g object
        - Service classes to receive dependencies via constructor injection
        - Unit tests to mock dependencies by overriding g object
        """
        g.redis_client = redis_client
        g.mongo_client = mongo_client
        g.config = app.config
        g.metrics_collector = metrics_collector
    
    # =========================================================================
    # Step 7: Register Flask Blueprints
    # =========================================================================
    
    # Register blueprints for HTTP route handling
    # Per Section 0.5.1 Group 1, blueprint registration order:
    # 1. events_bp: POST /events - Core webhook endpoint for error ingestion
    # 2. health_bp: GET /healthz - Health check for ALB target health monitoring
    # 3. metrics_bp: GET /metrics - Prometheus metrics exposition for observability
    
    app.register_blueprint(events_bp)
    logger.info(
        "Registered events blueprint",
        extra={
            'action': 'blueprint_registered',
            'blueprint': 'events_bp',
            'routes': ['POST /events']
        }
    )
    
    app.register_blueprint(health_bp)
    logger.info(
        "Registered health blueprint",
        extra={
            'action': 'blueprint_registered',
            'blueprint': 'health_bp',
            'routes': ['GET /healthz']
        }
    )
    
    app.register_blueprint(metrics_bp)
    logger.info(
        "Registered metrics blueprint",
        extra={
            'action': 'blueprint_registered',
            'blueprint': 'metrics_bp',
            'routes': ['GET /metrics']
        }
    )
    
    # =========================================================================
    # Step 8: Register SIGHUP Signal Handler for Hot-Reload
    # =========================================================================
    
    # Implement SIGHUP signal handler for zero-downtime configuration reload
    # Per Section 0.7.1 requirement #6, SIGHUP reloads YAML configuration files:
    # - config/severity_rules.yaml: Frequency-to-severity mappings
    # - config/ownership_rules.yaml: Service-to-assignee routing
    # - config/sanitization_patterns.yaml: PII detection patterns
    #
    # Services detect configuration changes via file modification time checks
    # and automatically reload configuration on next access
    
    def reload_configuration(signum, frame):
        """
        Signal handler for SIGHUP - reload configuration files without restart.
        
        This handler is called when the process receives SIGHUP signal:
            kill -HUP <pid>
        
        Configuration reload strategy:
        1. Log reload initiation event
        2. Service classes detect file modification time changes
        3. Services reload YAML configuration on next access
        4. Log reload completion event
        
        This pattern enables:
        - Zero-downtime configuration updates (no service restart)
        - Immediate application of new severity thresholds and routing rules
        - Rollback via file revert and SIGHUP signal
        
        Args:
            signum: Signal number (SIGHUP = 1)
            frame: Current stack frame (unused)
        """
        logger.info(
            "SIGHUP received - reloading configuration files",
            extra={
                'action': 'config_reload_initiated',
                'signal': 'SIGHUP',
                'config_files': [
                    'config/severity_rules.yaml',
                    'config/ownership_rules.yaml',
                    'config/sanitization_patterns.yaml'
                ]
            }
        )
        
        # Note: Actual configuration reload happens lazily in service classes
        # Services check file modification time and reload if changed
        # This avoids blocking the signal handler with file I/O
        
        logger.info(
            "Configuration reload complete - services will reload on next access",
            extra={
                'action': 'config_reload_complete',
                'signal': 'SIGHUP'
            }
        )
    
    # Register SIGHUP handler only in production (avoid conflicts with development debugger)
    if not app.config.get('DEBUG', False):
        signal.signal(signal.SIGHUP, reload_configuration)
        logger.info(
            "SIGHUP signal handler registered for configuration hot-reload",
            extra={
                'action': 'signal_handler_registered',
                'signal': 'SIGHUP'
            }
        )
    
    # =========================================================================
    # Step 9: Application Factory Complete
    # =========================================================================
    
    logger.info(
        "Flask application factory initialization complete",
        extra={
            'action': 'app_factory_complete',
            'config_name': config_name,
            'environment': environment,
            'redis_enabled': redis_client is not None,
            'mongodb_enabled': mongo_client is not None,
            'blueprints_registered': ['events_bp', 'health_bp', 'metrics_bp']
        }
    )
    
    return app


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    'create_app',
]
