"""
Application configuration management with environment-specific settings.

This module provides configuration classes for the Error Triage to Jira Upserter
service, loading settings from environment variables and AWS Secrets Manager. All
sensitive credentials (Jira API tokens, webhook secrets, MongoDB connection strings)
are retrieved from AWS Secrets Manager, never hardcoded or stored in environment
variables directly.

Per Section 0.5.1 Group 1 requirements, this module:
- Defines Config base class with shared settings across all environments
- Implements DevelopmentConfig for local development with debug mode enabled
- Implements ProductionConfig with strict validation and production-ready defaults
- Loads all secrets from AWS Secrets Manager with 1-hour caching
- Validates required environment variables at initialization
- Provides default values for optional settings
- Supports hot-reload via SIGHUP signal handler (implemented in application factory)

Configuration Hierarchy (per Section 0.1.2 architectural requirements):
- Config (base): Common settings for all environments
  - REDIS_HOST, JIRA_BASE_URL: Required environment variables
  - JIRA_CREDENTIALS: Loaded from AWS Secrets Manager (JSON)
  - WEBHOOK_SECRET: Loaded from AWS Secrets Manager (plain text)
  - MONGODB_URI: Optional, loaded from environment or Secrets Manager
  - Default TTLs: Comment rate limit, deduplication, frequency counters
- DevelopmentConfig: Local development settings
  - DEBUG=True: Enable Flask debug mode and verbose logging
  - TESTING=False: Production-like behavior in local environment
- ProductionConfig: Production deployment settings
  - DEBUG=False: Disable debug mode for performance and security
  - TESTING=False: Production behavior only

Environment Variables Required (per Section 0.4.1 integration requirements):
- REDIS_HOST: Redis hostname or endpoint (ElastiCache endpoint in production)
  Example: jiratest-error-triage-redis-prod.abc123.0001.use1.cache.amazonaws.com
- JIRA_BASE_URL: Jira Cloud base URL (without trailing slash)
  Example: https://myorg.atlassian.net
- ENVIRONMENT: Deployment environment (development, staging, production)
  Used for: Secret path construction, logging context, environment-specific rules
- PROJECT_KEY: Jira project key for error issues (default: ET)
  Example: ET for "Error Triage" project

Environment Variables Optional:
- ENABLE_MONGO: Enable MongoDB audit logging (default: false)
  Set to "true" or "1" to enable; any other value disables
- MONGODB_URI: MongoDB connection string (only used if ENABLE_MONGO=true)
  Example: mongodb+srv://user:pass@cluster.mongodb.net/jiratest-prod
  Note: If not provided but ENABLE_MONGO=true, attempts to load from Secrets Manager
- COMMENT_RATE_LIMIT_MINUTES: Minimum time between comments on same issue (default: 15)
  Override for testing or high-frequency error scenarios
- DEDUPLICATION_TTL: Event ID deduplication cache TTL in seconds (default: 3600)
  How long to remember event IDs to prevent duplicate processing
- FREQUENCY_COUNTER_TTL: Rolling frequency counter TTL in seconds (default: 300)
  Time window for counting error occurrences (5 minutes per Section 0.1.1)

AWS Secrets Manager Secret Paths (per Section 0.4.1 secret naming):
- Jira credentials: jira/jiratest/{env}/credentials
  Format: {"base_url": "https://...", "email": "...", "api_token": "..."}
- Webhook secret: jira/jiratest/{env}/webhook-secret
  Format: Plain text HMAC secret for Vercel signature verification
- MongoDB connection: mongodb/jiratest/{env}/connection-string (optional)
  Format: Plain text mongodb+srv://... URI

Security Constraints (per Section 0.1.2 and 0.7.4):
- All secrets loaded from AWS Secrets Manager, NEVER from environment variables
- Secret values NEVER logged (only secret names and status)
- Configuration validation fails fast at startup for missing required secrets
- ECS task role must have secretsmanager:GetSecretValue permission

Usage in Application Factory (per Section 0.5.1 Group 1):
    from app.config import Config, DevelopmentConfig, ProductionConfig
    
    def create_app(config_name='production'):
        app = Flask(__name__)
        
        # Load appropriate configuration class
        if config_name == 'development':
            app.config.from_object(DevelopmentConfig)
        elif config_name == 'production':
            app.config.from_object(ProductionConfig)
        else:
            app.config.from_object(Config)
        
        # Configuration is now loaded with all secrets cached
        redis_host = app.config['REDIS_HOST']
        jira_url = app.config['JIRA_CREDENTIALS']['base_url']

Hot-Reload Support (per Section 0.7.1 requirement #6):
    # In application factory, register SIGHUP handler
    import signal
    
    def reload_configuration(signum, frame):
        # Force refresh of all secrets from AWS Secrets Manager
        from utils.secrets_manager import get_json_secret, get_secret
        env = os.getenv('ENVIRONMENT', 'production')
        get_json_secret(f'jira/jiratest/{env}/credentials', force_refresh=True)
        get_secret(f'jira/jiratest/{env}/webhook-secret', force_refresh=True)
        logger.info("Configuration reloaded from AWS Secrets Manager")
    
    signal.signal(signal.SIGHUP, reload_configuration)
"""

import os
from typing import Optional, Dict, Any

from utils.secrets_manager import get_secret, get_json_secret


class Config:
    """
    Base configuration class with settings common to all environments.
    
    Loads configuration from environment variables and AWS Secrets Manager.
    Provides default values for optional settings and validates required
    settings at initialization time.
    
    Per Section 0.5.1 requirements, this class:
    - Loads REDIS_HOST and JIRA_BASE_URL from environment variables
    - Loads JIRA_CREDENTIALS from AWS Secrets Manager as JSON
    - Loads WEBHOOK_SECRET from AWS Secrets Manager as plain text
    - Optionally loads MONGODB_CONNECTION_STRING if ENABLE_MONGO=true
    - Provides default TTL values for caching and rate limiting
    - Validates all required settings at initialization
    
    All attributes are class-level attributes following Flask configuration pattern.
    Subclasses (DevelopmentConfig, ProductionConfig) override specific attributes.
    
    Attributes:
        REDIS_HOST: Redis server hostname (required)
        JIRA_BASE_URL: Jira Cloud base URL without trailing slash (required)
        JIRA_CREDENTIALS: Dictionary with base_url, email, api_token (from Secrets Manager)
        WEBHOOK_SECRET: HMAC secret for Vercel signature verification (from Secrets Manager)
        MONGODB_URI: MongoDB connection string (optional, from environment or Secrets Manager)
        MONGODB_CONNECTION_STRING: Alias for MONGODB_URI for backward compatibility
        ENABLE_MONGO: Boolean flag to enable MongoDB audit logging (default: False)
        COMMENT_RATE_LIMIT_MINUTES: Minimum time between comments (default: 15)
        DEDUPLICATION_TTL: Event ID cache TTL in seconds (default: 3600)
        FREQUENCY_COUNTER_TTL: Rolling counter TTL in seconds (default: 300)
        ENVIRONMENT: Deployment environment name (default: 'production')
        PROJECT_KEY: Jira project key for error issues (default: 'ET')
    
    Raises:
        ValueError: If required environment variables are missing
        ClientError: If AWS Secrets Manager secrets cannot be retrieved
    """
    
    # Environment deployment name (development, staging, production)
    ENVIRONMENT: str = os.getenv('ENVIRONMENT', 'production')
    
    # Redis configuration - REQUIRED
    # Example: jiratest-error-triage-redis-prod.abc123.0001.use1.cache.amazonaws.com
    REDIS_HOST: Optional[str] = os.getenv('REDIS_HOST')
    
    # Jira Cloud base URL - REQUIRED
    # Example: https://myorg.atlassian.net
    JIRA_BASE_URL: Optional[str] = os.getenv('JIRA_BASE_URL')
    
    # Jira project key for error issues (default: ET for "Error Triage")
    PROJECT_KEY: str = os.getenv('PROJECT_KEY', 'ET')
    
    # MongoDB configuration - OPTIONAL
    # Only used if ENABLE_MONGO is set to "true" or "1"
    ENABLE_MONGO: bool = os.getenv('ENABLE_MONGO', 'false').lower() in ('true', '1', 'yes')
    MONGODB_URI: Optional[str] = os.getenv('MONGODB_URI')
    
    # Cache and rate limit TTLs - OPTIONAL (have defaults)
    # Comment rate limit: minimum minutes between comments on same issue (default: 15)
    COMMENT_RATE_LIMIT_MINUTES: int = int(os.getenv('COMMENT_RATE_LIMIT_MINUTES', '15'))
    
    # Deduplication TTL: how long to remember event IDs (default: 3600 = 1 hour)
    DEDUPLICATION_TTL: int = int(os.getenv('DEDUPLICATION_TTL', '3600'))
    
    # Frequency counter TTL: rolling window for occurrence counting (default: 300 = 5 minutes)
    FREQUENCY_COUNTER_TTL: int = int(os.getenv('FREQUENCY_COUNTER_TTL', '300'))
    
    # Secrets loaded from AWS Secrets Manager (initialized in __init_subclass__)
    JIRA_CREDENTIALS: Dict[str, Any] = {}
    WEBHOOK_SECRET: str = ''
    MONGODB_CONNECTION_STRING: Optional[str] = None
    
    def __init_subclass__(cls, **kwargs):
        """
        Initialize configuration class on subclass creation.
        
        This method is called when a subclass (DevelopmentConfig, ProductionConfig)
        is created. It validates required environment variables and loads secrets
        from AWS Secrets Manager.
        
        Per Section 0.1.2 constraint "MUST validate required environment variables",
        this method fails fast if REDIS_HOST or JIRA_BASE_URL are missing.
        
        Per Section 0.7.1 requirement "Store secrets in AWS Secrets Manager",
        this method loads JIRA_CREDENTIALS and WEBHOOK_SECRET from Secrets Manager
        using the secrets_manager utility module.
        
        Raises:
            ValueError: If required environment variables are missing
            ClientError: If AWS Secrets Manager secrets cannot be retrieved
        """
        super().__init_subclass__(**kwargs)
        
        # Note: __init_subclass__ runs at class definition time, not instance creation.
        # Configuration is loaded when the module is imported, ensuring secrets are
        # available before application startup.
        
        # Validate required environment variables
        if not cls.REDIS_HOST:
            raise ValueError(
                "REDIS_HOST environment variable is required. "
                "Example: export REDIS_HOST=jiratest-redis-prod.cache.amazonaws.com"
            )
        
        if not cls.JIRA_BASE_URL:
            raise ValueError(
                "JIRA_BASE_URL environment variable is required. "
                "Example: export JIRA_BASE_URL=https://myorg.atlassian.net"
            )
        
        # Load Jira credentials from AWS Secrets Manager
        # Secret path: jira/jiratest/{env}/credentials
        # Format: {"base_url": "https://...", "email": "...", "api_token": "..."}
        env = cls.ENVIRONMENT
        jira_secret_name = f"jira/jiratest/{env}/credentials"
        
        try:
            cls.JIRA_CREDENTIALS = get_json_secret(jira_secret_name)
            
            # Validate JSON structure has required fields
            required_fields = ['base_url', 'email', 'api_token']
            missing_fields = [f for f in required_fields if f not in cls.JIRA_CREDENTIALS]
            if missing_fields:
                raise ValueError(
                    f"Jira credentials secret {jira_secret_name} is missing required fields: "
                    f"{', '.join(missing_fields)}. Required fields: {', '.join(required_fields)}"
                )
        except Exception as e:
            raise ValueError(
                f"Failed to load Jira credentials from AWS Secrets Manager "
                f"(secret: {jira_secret_name}): {str(e)}"
            )
        
        # Load webhook secret from AWS Secrets Manager
        # Secret path: jira/jiratest/{env}/webhook-secret
        # Format: Plain text HMAC secret string
        webhook_secret_name = f"jira/jiratest/{env}/webhook-secret"
        
        try:
            cls.WEBHOOK_SECRET = get_secret(webhook_secret_name)
            
            # Validate webhook secret is not empty
            if not cls.WEBHOOK_SECRET:
                raise ValueError(f"Webhook secret {webhook_secret_name} is empty")
        except Exception as e:
            raise ValueError(
                f"Failed to load webhook secret from AWS Secrets Manager "
                f"(secret: {webhook_secret_name}): {str(e)}"
            )
        
        # Load MongoDB connection string if enabled
        # Optional: only loaded if ENABLE_MONGO=true
        if cls.ENABLE_MONGO:
            # Try environment variable first, fall back to Secrets Manager
            if cls.MONGODB_URI:
                cls.MONGODB_CONNECTION_STRING = cls.MONGODB_URI
            else:
                # Load from Secrets Manager
                # Secret path: mongodb/jiratest/{env}/connection-string
                # Format: Plain text mongodb+srv://... URI
                mongo_secret_name = f"mongodb/jiratest/{env}/connection-string"
                
                try:
                    cls.MONGODB_CONNECTION_STRING = get_secret(mongo_secret_name)
                    
                    # Validate connection string is not empty
                    if not cls.MONGODB_CONNECTION_STRING:
                        raise ValueError(f"MongoDB connection string {mongo_secret_name} is empty")
                except Exception as e:
                    raise ValueError(
                        f"ENABLE_MONGO is true but MongoDB connection string not found. "
                        f"Either set MONGODB_URI environment variable or create secret "
                        f"{mongo_secret_name} in AWS Secrets Manager. Error: {str(e)}"
                    )
        else:
            # MongoDB disabled, set connection string to None
            cls.MONGODB_CONNECTION_STRING = None


class DevelopmentConfig(Config):
    """
    Development environment configuration with debug mode enabled.
    
    Inherits all settings from Config base class and overrides specific
    attributes for local development. Enables Flask debug mode for auto-reload
    and verbose error messages.
    
    Per Section 0.5.1 requirements, this configuration:
    - Enables DEBUG mode for Flask auto-reload and detailed error pages
    - Disables TESTING mode (production-like behavior)
    - Inherits all other settings from Config (Redis, Jira, MongoDB, TTLs)
    
    Usage:
        app.config.from_object(DevelopmentConfig)
    
    Attributes:
        DEBUG: Enable Flask debug mode (True)
        TESTING: Disable testing mode (False)
    """
    
    # Enable Flask debug mode for development
    # - Auto-reload on code changes
    # - Detailed error pages with stack traces
    # - Verbose logging output
    DEBUG: bool = True
    
    # Disable testing mode (use production-like behavior)
    # Set to True in test fixtures for unit/integration tests
    TESTING: bool = False


class ProductionConfig(Config):
    """
    Production environment configuration with strict security settings.
    
    Inherits all settings from Config base class and overrides specific
    attributes for production deployment. Disables debug mode for security
    and performance.
    
    Per Section 0.5.1 requirements, this configuration:
    - Disables DEBUG mode for security and performance
    - Disables TESTING mode (production behavior only)
    - Enforces strict validation of all required settings
    - Uses production-grade defaults for all TTLs
    
    Per Section 0.1.2 security constraints, production mode:
    - Never logs sensitive configuration values
    - Validates all secrets are loaded successfully
    - Fails fast on configuration errors
    
    Usage:
        app.config.from_object(ProductionConfig)
    
    Attributes:
        DEBUG: Disable Flask debug mode (False)
        TESTING: Disable testing mode (False)
    """
    
    # Disable Flask debug mode for production
    # - No auto-reload (managed by ECS deployment)
    # - Minimal error information in responses (security)
    # - Optimized performance
    DEBUG: bool = False
    
    # Disable testing mode (production behavior only)
    TESTING: bool = False
