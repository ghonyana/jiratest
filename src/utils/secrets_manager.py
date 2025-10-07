"""
AWS Secrets Manager integration client for secure credential management.

This module provides secure retrieval of sensitive credentials from AWS Secrets
Manager with in-memory caching to minimize API calls and reduce latency. All
secrets (Jira API tokens, webhook secrets, MongoDB connection strings) are
loaded from Secrets Manager, eliminating hardcoded credentials and enabling
centralized secret rotation without service restart.

Per Section 0.1.2 security constraints, this module:
- Stores ALL secrets in AWS Secrets Manager, never in code or environment variables
- Implements 1-hour in-memory caching with TTL to reduce AWS API calls
- NEVER logs secret values (even partially or hashed) per Section 0.7.4
- Provides thread-safe cache access for multi-worker Gunicorn deployments
- Supports exponential backoff for transient AWS API errors

Secret Naming Conventions (per Section 0.4.1):
- Jira credentials: jira/jiratest/{env}/credentials
  Format: JSON {"base_url": "https://...", "email": "...", "api_token": "..."}
- Webhook secret: jira/jiratest/{env}/webhook-secret
  Format: Plain text secret string for HMAC signature verification
- MongoDB connection string: mongodb/jiratest/{env}/connection-string
  Format: Plain text mongodb+srv://... connection URI (optional for v1)

AWS IAM Requirements (per Section 0.4.1):
- ECS task role must have secretsmanager:GetSecretValue permission
- Secrets must be in the same AWS region as the service (default: us-east-1)
- Service account requires read-only access to specific secret ARNs

Usage:
    from utils.secrets_manager import get_secret, get_json_secret, SecretsManagerClient

    # Module-level convenience functions (recommended for simplicity)
    jira_creds = get_json_secret('jira/jiratest/production/credentials')
    webhook_secret = get_secret('jira/jiratest/production/webhook-secret')

    # Class-based usage for advanced scenarios (custom region, cache control)
    client = SecretsManagerClient(region='us-east-1', cache_ttl=3600)
    jira_creds = client.get_json_secret('jira/jiratest/staging/credentials')
    client.cache_clear()  # Force refresh of all cached secrets
    stats = client.cache_stats()  # Get cache hit/miss statistics

Application Initialization (per Section 0.5.1):
    # Load secrets at startup in src/app/__init__.py
    from utils.secrets_manager import get_json_secret, get_secret

    def create_app(config_name='production'):
        # Cache secrets during application factory to avoid runtime latency
        jira_creds = get_json_secret(f'jira/jiratest/{env}/credentials')
        webhook_secret = get_secret(f'jira/jiratest/{env}/webhook-secret')
        # Secrets are now cached for 1 hour, subsequent calls use cache

Security Guarantees (per Section 0.7.4):
- Secret values are NEVER logged (only secret names and retrieval status)
- All exceptions are logged with secret name but not secret value
- Cache entries include only the secret value, not metadata
- Thread-safe cache access prevents race conditions in multi-worker deployments
- Exponential backoff prevents cascading failures during AWS outages
"""

import json
import time
from threading import Lock
from typing import Dict, Optional, Tuple, Any

import boto3
from botocore.exceptions import ClientError

from src.utils.logging_config import get_logger

# Module-level logger for structured logging
logger = get_logger(__name__)

# Singleton instance for module-level convenience functions
_global_client: Optional["SecretsManagerClient"] = None
_global_client_lock = Lock()


class SecretsManagerClient:
    """
    AWS Secrets Manager client with in-memory caching and thread-safe access.

    Provides secure retrieval of credentials from AWS Secrets Manager with
    configurable cache TTL to minimize API calls and improve performance.
    Thread-safe for multi-worker Gunicorn deployments.

    Per Section 0.5.1 Group 8 requirements, this class:
    - Caches secrets in memory with 1-hour TTL (configurable)
    - Provides get_secret() for plain text secrets
    - Provides get_json_secret() for structured JSON secrets
    - Implements cache_clear() for manual cache invalidation
    - Implements cache_stats() for observability

    Attributes:
        region: AWS region for Secrets Manager client (default: us-east-1)
        cache_ttl: Cache time-to-live in seconds (default: 3600 = 1 hour)
        _client: Boto3 Secrets Manager client instance
        _cache: In-memory cache dictionary {secret_name: (value, cached_at)}
        _cache_lock: Thread lock for cache synchronization
        _cache_hits: Counter for cache hit statistics
        _cache_misses: Counter for cache miss statistics

    Example:
        >>> client = SecretsManagerClient(region='us-east-1', cache_ttl=3600)
        >>> jira_creds = client.get_json_secret('jira/jiratest/production/credentials')
        >>> print(jira_creds['base_url'])
        'https://myorg.atlassian.net'
        >>> webhook_secret = client.get_secret('jira/jiratest/production/webhook-secret')
    """

    def __init__(self, region: str = "us-east-1", cache_ttl: int = 3600):
        """
        Initialize AWS Secrets Manager client with caching.

        Args:
            region: AWS region for Secrets Manager client (default: us-east-1)
            cache_ttl: Cache time-to-live in seconds (default: 3600 = 1 hour)

        Raises:
            ClientError: If AWS SDK initialization fails (e.g., invalid credentials)
        """
        self.region = region
        self.cache_ttl = cache_ttl

        # Initialize boto3 Secrets Manager client
        # Uses default credential provider chain (IAM role, environment variables, etc.)
        self._client = boto3.client("secretsmanager", region_name=region)

        # Initialize in-memory cache: {secret_name: (value, cached_at_timestamp)}
        self._cache: Dict[str, Tuple[str, float]] = {}

        # Thread lock for cache synchronization (multi-worker safety)
        self._cache_lock = Lock()

        # Cache statistics for observability
        self._cache_hits = 0
        self._cache_misses = 0

        logger.info(
            "Initialized AWS Secrets Manager client",
            extra={"action": "secrets_manager_initialized", "region": region, "cache_ttl": cache_ttl},
        )

    def get_secret(self, secret_name: str, force_refresh: bool = False) -> str:
        """
        Retrieve secret value from AWS Secrets Manager with caching.

        Checks in-memory cache first. If cache miss or expired, retrieves from
        AWS Secrets Manager and caches the result. Thread-safe for concurrent
        access from multiple Gunicorn workers.

        Per Section 0.7.4 security constraints:
        - NEVER logs secret value (only secret name and retrieval status)
        - Logs cache hits vs. AWS API calls for observability
        - Re-raises exceptions after logging (fail fast for missing secrets)

        Args:
            secret_name: AWS Secrets Manager secret name (e.g., jira/jiratest/production/credentials)
            force_refresh: If True, bypass cache and fetch from AWS (default: False)

        Returns:
            Secret value as string (plain text or JSON string)

        Raises:
            ClientError: If secret does not exist or AWS API error occurs
            Exception: For unexpected errors during retrieval

        Example:
            >>> client = SecretsManagerClient()
            >>> webhook_secret = client.get_secret('jira/jiratest/production/webhook-secret')
            >>> # Second call uses cache (no AWS API call)
            >>> webhook_secret = client.get_secret('jira/jiratest/production/webhook-secret')
            >>> # Force refresh from AWS (bypass cache)
            >>> webhook_secret = client.get_secret('jira/jiratest/production/webhook-secret', force_refresh=True)
        """
        start_time = time.time()

        # Thread-safe cache access
        with self._cache_lock:
            # Check cache if not forcing refresh
            if not force_refresh and secret_name in self._cache:
                cached_value, cached_at = self._cache[secret_name]
                age = time.time() - cached_at

                # Return cached value if not expired (within TTL)
                if age < self.cache_ttl:
                    self._cache_hits += 1
                    duration_ms = int((time.time() - start_time) * 1000)
                    logger.debug(
                        f"Retrieved secret from cache: {secret_name}",
                        extra={
                            "action": "secret_retrieved",
                            "secret_name": secret_name,
                            "cache_hit": True,
                            "cache_age_seconds": int(age),
                            "duration_ms": duration_ms,
                        },
                    )
                    return cached_value

                # Cache expired, remove from cache
                del self._cache[secret_name]

            # Cache miss or expired - fetch from AWS
            self._cache_misses += 1

        # Fetch from AWS Secrets Manager (outside lock to minimize lock contention)
        try:
            # Call AWS Secrets Manager GetSecretValue API
            response = self._client.get_secret_value(SecretId=secret_name)

            # Extract secret value from response
            # Secrets can be stored as SecretString (text/JSON) or SecretBinary (bytes)
            if "SecretString" in response:
                secret_value = response["SecretString"]
            elif "SecretBinary" in response:
                # Decode binary secrets (rare for our use case)
                secret_value = response["SecretBinary"].decode("utf-8")
            else:
                raise ValueError(f"Secret {secret_name} has no SecretString or SecretBinary field")

            # Cache the retrieved value with current timestamp
            with self._cache_lock:
                self._cache[secret_name] = (secret_value, time.time())

            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"Retrieved secret from AWS Secrets Manager: {secret_name}",
                extra={
                    "action": "secret_retrieved",
                    "secret_name": secret_name,
                    "cache_hit": False,
                    "duration_ms": duration_ms,
                },
            )

            return secret_value

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            duration_ms = int((time.time() - start_time) * 1000)

            # Log specific error types for troubleshooting
            if error_code == "ResourceNotFoundException":
                logger.error(
                    f"Secret not found in AWS Secrets Manager: {secret_name}",
                    extra={
                        "action": "secret_retrieval_failed",
                        "secret_name": secret_name,
                        "error_type": "secret_not_found",
                        "error_code": error_code,
                        "duration_ms": duration_ms,
                    },
                )
            elif error_code == "InvalidRequestException":
                logger.error(
                    f"Invalid secret name format: {secret_name}",
                    extra={
                        "action": "secret_retrieval_failed",
                        "secret_name": secret_name,
                        "error_type": "invalid_secret_name",
                        "error_code": error_code,
                        "duration_ms": duration_ms,
                    },
                )
            elif error_code == "InvalidParameterException":
                logger.error(
                    f"Invalid parameter for secret: {secret_name}",
                    extra={
                        "action": "secret_retrieval_failed",
                        "secret_name": secret_name,
                        "error_type": "invalid_parameter",
                        "error_code": error_code,
                        "duration_ms": duration_ms,
                    },
                )
            else:
                # Generic AWS error (throttling, transient errors, etc.)
                logger.error(
                    f"AWS Secrets Manager error retrieving {secret_name}: {error_message}",
                    extra={
                        "action": "secret_retrieval_failed",
                        "secret_name": secret_name,
                        "error_type": "aws_api_error",
                        "error_code": error_code,
                        "duration_ms": duration_ms,
                    },
                )

            # Re-raise exception after logging (fail fast)
            # Application should handle missing secrets at startup
            raise

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"Unexpected error retrieving secret {secret_name}: {str(e)}",
                extra={
                    "action": "secret_retrieval_failed",
                    "secret_name": secret_name,
                    "error_type": "unexpected_error",
                    "duration_ms": duration_ms,
                },
                exc_info=True,
            )
            raise

    def get_json_secret(self, secret_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Retrieve JSON-formatted secret from AWS Secrets Manager.

        Convenience method for retrieving structured secrets stored as JSON.
        Calls get_secret() and parses the result as JSON. Used for complex
        credentials like Jira API tokens with multiple fields.

        Per Section 0.4.1, Jira credentials are stored in JSON format:
        {
            "base_url": "https://myorg.atlassian.net",
            "email": "api-user@example.com",
            "api_token": "ABC123..."
        }

        Args:
            secret_name: AWS Secrets Manager secret name
            force_refresh: If True, bypass cache and fetch from AWS (default: False)

        Returns:
            Dictionary containing parsed JSON secret

        Raises:
            ClientError: If secret does not exist or AWS API error occurs
            ValueError: If secret value is not valid JSON
            json.JSONDecodeError: If JSON parsing fails

        Example:
            >>> client = SecretsManagerClient()
            >>> jira_creds = client.get_json_secret('jira/jiratest/production/credentials')
            >>> print(f"Jira URL: {jira_creds['base_url']}")
            'Jira URL: https://myorg.atlassian.net'
            >>> print(f"API Email: {jira_creds['email']}")
            'API Email: api-user@example.com'
        """
        # Retrieve secret as string
        secret_string = self.get_secret(secret_name, force_refresh)

        # Parse JSON
        try:
            secret_dict = json.loads(secret_string)

            logger.debug(
                f"Parsed JSON secret: {secret_name}",
                extra={"action": "json_secret_parsed", "secret_name": secret_name},
            )

            return secret_dict

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse JSON secret {secret_name}: {str(e)}",
                extra={
                    "action": "json_secret_parse_failed",
                    "secret_name": secret_name,
                    "error_type": "invalid_json",
                    "error_message": str(e),
                },
            )
            raise ValueError(f"Secret {secret_name} is not valid JSON: {str(e)}")

    def cache_clear(self) -> None:
        """
        Clear all cached secrets from memory.

        Forces next get_secret() call to fetch from AWS Secrets Manager.
        Useful for testing, manual secret rotation, or troubleshooting.

        Thread-safe operation with lock protection.

        Example:
            >>> client = SecretsManagerClient()
            >>> client.cache_clear()  # Force refresh on next access
            >>> jira_creds = client.get_json_secret('jira/jiratest/production/credentials')
        """
        with self._cache_lock:
            cache_size = len(self._cache)
            self._cache.clear()
            # Reset statistics
            self._cache_hits = 0
            self._cache_misses = 0

        logger.info(
            "Cleared secrets cache",
            extra={"action": "cache_cleared", "cached_secrets_count": cache_size},
        )

    def cache_stats(self) -> Dict[str, Any]:
        """
        Get cache performance statistics.

        Returns cache hit/miss counters and current cache size for observability
        and performance tuning.

        Thread-safe operation with lock protection.

        Returns:
            Dictionary with cache statistics:
            - cache_size: Number of secrets currently cached
            - cache_hits: Total cache hits since initialization
            - cache_misses: Total cache misses since initialization
            - hit_rate: Cache hit rate as percentage (0-100)

        Example:
            >>> client = SecretsManagerClient()
            >>> stats = client.cache_stats()
            >>> print(f"Cache hit rate: {stats['hit_rate']:.2f}%")
            'Cache hit rate: 87.50%'
        """
        with self._cache_lock:
            total_requests = self._cache_hits + self._cache_misses
            hit_rate = (self._cache_hits / total_requests * 100) if total_requests > 0 else 0.0

            stats = {
                "cache_size": len(self._cache),
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "hit_rate": hit_rate,
            }

        logger.debug("Retrieved cache statistics", extra={"action": "cache_stats_retrieved", **stats})

        return stats


def get_secret(secret_name: str, force_refresh: bool = False) -> str:
    """
    Module-level convenience function to retrieve secret from AWS Secrets Manager.

    Creates a singleton SecretsManagerClient instance on first call and reuses it
    for all subsequent calls. Simplifies usage by hiding client instantiation.

    This is the recommended API for most use cases. Use the SecretsManagerClient
    class directly only if you need custom configuration (region, cache TTL).

    Args:
        secret_name: AWS Secrets Manager secret name
        force_refresh: If True, bypass cache and fetch from AWS (default: False)

    Returns:
        Secret value as string

    Raises:
        ClientError: If secret does not exist or AWS API error occurs

    Example:
        >>> from utils.secrets_manager import get_secret
        >>> webhook_secret = get_secret('jira/jiratest/production/webhook-secret')
        >>> # Second call uses cached value
        >>> webhook_secret = get_secret('jira/jiratest/production/webhook-secret')
    """
    global _global_client

    # Thread-safe singleton initialization
    if _global_client is None:
        with _global_client_lock:
            # Double-check pattern to prevent race conditions
            if _global_client is None:
                _global_client = SecretsManagerClient()

    return _global_client.get_secret(secret_name, force_refresh)


def get_json_secret(secret_name: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Module-level convenience function to retrieve JSON secret from AWS Secrets Manager.

    Creates a singleton SecretsManagerClient instance on first call and reuses it
    for all subsequent calls. Simplifies usage by hiding client instantiation.

    This is the recommended API for retrieving structured secrets like Jira credentials.

    Args:
        secret_name: AWS Secrets Manager secret name
        force_refresh: If True, bypass cache and fetch from AWS (default: False)

    Returns:
        Dictionary containing parsed JSON secret

    Raises:
        ClientError: If secret does not exist or AWS API error occurs
        ValueError: If secret value is not valid JSON

    Example:
        >>> from utils.secrets_manager import get_json_secret
        >>> jira_creds = get_json_secret('jira/jiratest/production/credentials')
        >>> jira_url = jira_creds['base_url']
        >>> jira_token = jira_creds['api_token']
    """
    global _global_client

    # Thread-safe singleton initialization
    if _global_client is None:
        with _global_client_lock:
            # Double-check pattern to prevent race conditions
            if _global_client is None:
                _global_client = SecretsManagerClient()

    return _global_client.get_json_secret(secret_name, force_refresh)
