"""
Flask blueprint for comprehensive health check endpoint at GET /healthz.

This module implements health check validation for all critical service dependencies
(Redis, MongoDB, Jira) with per-dependency latency measurements, enabling Application
Load Balancer target health determination and ECS task lifecycle management.

Per Section 0.1.1 (Operational Excellence) and Section 6.5.5.1:
- Validates connectivity to Redis (PING command with 2-second timeout)
- Validates connectivity to MongoDB (admin ping with 5-second timeout, optional)
- Validates connectivity to Jira API (serverInfo endpoint with 5-second timeout)
- Returns 200 OK when required dependencies (Redis, Jira) are UP
- Returns 503 Service Unavailable when any required dependency fails
- Includes per-dependency latency_ms for observability

ALB Health Check Configuration (Section 6.5.5.1):
- Path: /healthz
- Interval: 30 seconds
- Timeout: 5 seconds
- Healthy threshold: 2 consecutive successes
- Unhealthy threshold: 3 consecutive failures

Response Format:
    {
      "status": "healthy" | "unhealthy",
      "timestamp": "2025-01-15T10:30:45.123Z",
      "version": "1.0.0",
      "checks": {
        "redis": {"status": "up", "latency_ms": 3},
        "mongodb": {"status": "up", "latency_ms": 15},
        "jira": {"status": "up", "latency_ms": 85}
      },
      "resources": {
        "cpu_percent": 45.2,
        "memory_percent": 67.8
      }
    }

Dependencies:
- Flask: Web framework for route handling and JSON responses
- Redis: Required dependency for frequency counters and deduplication
- MongoDB: Optional dependency for audit logs (controlled by ENABLE_MONGO)
- Jira: Required dependency for issue management
- psutil: Optional for resource metrics (CPU, memory)

Security:
- No authentication required (ALB health check probes)
- No sensitive information exposed in response
- Error messages are generic without stack traces

Author: Blitzy Platform
Version: 1.0.0
"""

import os
from datetime import datetime, timezone
from time import perf_counter
from typing import Dict, Any, Optional, Tuple

from flask import Blueprint, jsonify, Response
from redis import Redis
from pymongo import MongoClient
from jira import JIRA

# Internal imports - from depends_on_files only
from src.utils.logging_config import get_logger


# Initialize logger for this module with structured JSON logging
logger = get_logger(__name__)

# Flask Blueprint definition for health check routes
health_bp = Blueprint('health', __name__)

# Application version for health check response
APP_VERSION = "1.0.0"


def _check_redis(redis_client: Redis, timeout_seconds: int = 2) -> Tuple[str, Optional[float], Optional[str]]:
    """
    Check Redis connectivity using PING command with timeout.

    Validates Redis availability for frequency counters and event deduplication.
    Redis is a REQUIRED dependency; failure triggers 503 Service Unavailable.

    Per Section 0.5.1 Group 7, Redis PING must complete within 2 seconds.

    Args:
        redis_client: Injected Redis client instance from application factory
        timeout_seconds: Maximum time to wait for PING response (default: 2)

    Returns:
        Tuple of (status, latency_ms, error_message):
        - status: "up" if PING returns PONG, "down" otherwise
        - latency_ms: Elapsed time in milliseconds, None if failed
        - error_message: Generic error description if failed, None if successful

    Example:
        >>> status, latency, error = _check_redis(redis_client)
        >>> # ("up", 2.5, None)
    """
    start_time = perf_counter()
    status = "down"
    latency_ms = None
    error_message = None

    try:
        # Execute PING command with socket timeout
        # Redis client uses socket_timeout from connection pool configuration
        response = redis_client.ping()
        elapsed_ms = (perf_counter() - start_time) * 1000

        if response is True:  # Redis PING returns True for PONG
            status = "up"
            latency_ms = round(elapsed_ms, 2)
            logger.debug(
                "Redis health check passed",
                extra={
                    "action": "health_check_redis_success",
                    "duration_ms": latency_ms
                }
            )
        else:
            error_message = "PING returned unexpected response"
            logger.warning(
                "Redis health check returned unexpected response",
                extra={
                    "action": "health_check_redis_unexpected",
                    "response": str(response)
                }
            )

    except Exception as e:
        error_message = "Redis connection failed"
        logger.error(
            "Redis health check failed",
            extra={
                "action": "health_check_redis_failed",
                "error_type": type(e).__name__
            }
        )

    return status, latency_ms, error_message


def _check_mongodb(mongo_client: Optional[MongoClient], timeout_seconds: int = 5) -> Tuple[str, Optional[float], Optional[str]]:
    """
    Check MongoDB connectivity using admin ping command with timeout.

    Validates MongoDB availability for audit logs. MongoDB is an OPTIONAL
    dependency; failure reports "degraded" status without triggering 503.

    Per Section 0.5.1 Group 7 and Section 0.4.4, MongoDB check is skipped
    if ENABLE_MONGO=false environment variable is set.

    Args:
        mongo_client: Injected MongoDB client instance (None if disabled)
        timeout_seconds: Maximum time to wait for ping response (default: 5)

    Returns:
        Tuple of (status, latency_ms, error_message):
        - status: "up" if ping succeeds, "down" if fails, "disabled" if None
        - latency_ms: Elapsed time in milliseconds, None if failed or disabled
        - error_message: Generic error description if failed, None otherwise

    Example:
        >>> status, latency, error = _check_mongodb(mongo_client)
        >>> # ("up", 15.3, None)
        >>> status, latency, error = _check_mongodb(None)
        >>> # ("disabled", None, None)
    """
    if mongo_client is None:
        # MongoDB disabled via ENABLE_MONGO=false
        logger.debug(
            "MongoDB health check skipped (disabled)",
            extra={"action": "health_check_mongodb_disabled"}
        )
        return "disabled", None, None

    start_time = perf_counter()
    status = "down"
    latency_ms = None
    error_message = None

    try:
        # Execute admin ping command with server selection timeout
        # MongoDB admin().command() uses serverSelectionTimeoutMS from client config
        result = mongo_client.admin.command('ping')
        elapsed_ms = (perf_counter() - start_time) * 1000

        if result.get('ok') == 1:
            status = "up"
            latency_ms = round(elapsed_ms, 2)
            logger.debug(
                "MongoDB health check passed",
                extra={
                    "action": "health_check_mongodb_success",
                    "duration_ms": latency_ms
                }
            )
        else:
            error_message = "Ping returned unexpected response"
            logger.warning(
                "MongoDB health check returned unexpected response",
                extra={
                    "action": "health_check_mongodb_unexpected",
                    "response": str(result)
                }
            )

    except Exception as e:
        error_message = "MongoDB connection failed"
        logger.error(
            "MongoDB health check failed",
            extra={
                "action": "health_check_mongodb_failed",
                "error_type": type(e).__name__
            }
        )

    return status, latency_ms, error_message


def _check_jira(jira_client: JIRA, timeout_seconds: int = 5) -> Tuple[str, Optional[float], Optional[str]]:
    """
    Check Jira API connectivity using lightweight serverInfo endpoint.

    Validates Jira Cloud API availability for issue management. Jira is a
    REQUIRED dependency; failure triggers 503 Service Unavailable.

    Per Section 0.5.1 Group 7, uses GET /rest/api/2/serverInfo endpoint
    which is lightweight and doesn't require specific permissions.

    Args:
        jira_client: Injected Jira client instance from application factory
        timeout_seconds: Maximum time to wait for API response (default: 5)

    Returns:
        Tuple of (status, latency_ms, error_message):
        - status: "up" if HTTP 200 with valid JSON, "down" otherwise
        - latency_ms: Elapsed time in milliseconds, None if failed
        - error_message: Generic error description if failed, None if successful

    Example:
        >>> status, latency, error = _check_jira(jira_client)
        >>> # ("up", 85.7, None)
    """
    start_time = perf_counter()
    status = "down"
    latency_ms = None
    error_message = None

    try:
        # Call lightweight serverInfo endpoint
        # Returns server version and metadata without requiring permissions
        server_info = jira_client.server_info()
        elapsed_ms = (perf_counter() - start_time) * 1000

        # Validate response contains expected fields
        if server_info and 'baseUrl' in server_info:
            status = "up"
            latency_ms = round(elapsed_ms, 2)
            logger.debug(
                "Jira health check passed",
                extra={
                    "action": "health_check_jira_success",
                    "duration_ms": latency_ms
                }
            )
        else:
            error_message = "Server info returned invalid response"
            logger.warning(
                "Jira health check returned unexpected response",
                extra={
                    "action": "health_check_jira_unexpected",
                    "has_server_info": bool(server_info)
                }
            )

    except Exception as e:
        error_message = "Jira API connection failed"
        logger.error(
            "Jira health check failed",
            extra={
                "action": "health_check_jira_failed",
                "error_type": type(e).__name__
            }
        )

    return status, latency_ms, error_message


def _get_resource_metrics() -> Optional[Dict[str, float]]:
    """
    Collect CPU and memory utilization metrics using psutil.

    Provides optional operational context about container resource consumption
    in health check response. Gracefully omitted if psutil not installed.

    Per Section 0.5.1 Group 7, resource metrics are optional and do not
    affect health check status determination.

    Returns:
        Dictionary with cpu_percent and memory_percent, or None if psutil unavailable

    Example:
        >>> metrics = _get_resource_metrics()
        >>> # {"cpu_percent": 45.2, "memory_percent": 67.8}
    """
    try:
        import psutil

        # Get CPU percent with short interval (non-blocking)
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Get memory percent from virtual memory stats
        memory_info = psutil.virtual_memory()
        memory_percent = memory_info.percent

        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_percent": round(memory_percent, 1)
        }

    except ImportError:
        # psutil not installed - resource metrics are optional
        logger.debug(
            "Resource metrics unavailable (psutil not installed)",
            extra={"action": "health_check_resources_unavailable"}
        )
        return None

    except Exception as e:
        # Unexpected error collecting metrics - log and continue
        logger.warning(
            "Failed to collect resource metrics",
            extra={
                "action": "health_check_resources_failed",
                "error_type": type(e).__name__
            }
        )
        return None


@health_bp.route('/healthz', methods=['GET'])
def handle_health() -> Response:
    """
    Handle GET /healthz health check endpoint for ALB and ECS orchestration.

    Validates connectivity to all critical service dependencies with per-dependency
    latency measurements. Returns 200 OK when required dependencies (Redis, Jira)
    are UP, or 503 Service Unavailable when any required dependency fails.

    Per Section 6.5.5.1, this endpoint enables:
    - Application Load Balancer target health determination
    - ECS task health monitoring and automatic replacement
    - Service mesh health propagation
    - Operational dashboard status indicators

    Health Check Logic:
    1. Check Redis (required) - PING command with 2-second timeout
    2. Check MongoDB (optional) - admin ping with 5-second timeout
    3. Check Jira (required) - serverInfo API with 5-second timeout
    4. Collect resource metrics - CPU and memory utilization (optional)

    HTTP Status Codes:
    - 200 OK: Redis AND Jira are UP (MongoDB optional)
    - 503 Service Unavailable: Redis OR Jira are DOWN

    MongoDB Handling:
    - MongoDB failure reports "down" or "degraded" status
    - Does NOT trigger 503 (optional dependency for audit logs)
    - If ENABLE_MONGO=false, status shows "disabled"

    Response Format (200 OK):
        {
          "status": "healthy",
          "timestamp": "2025-01-15T10:30:45.123Z",
          "version": "1.0.0",
          "checks": {
            "redis": {"status": "up", "latency_ms": 3},
            "mongodb": {"status": "disabled"},
            "jira": {"status": "up", "latency_ms": 85}
          },
          "resources": {
            "cpu_percent": 45.2,
            "memory_percent": 67.8
          }
        }

    Response Format (503 Service Unavailable):
        {
          "status": "unhealthy",
          "timestamp": "2025-01-15T10:30:45.123Z",
          "version": "1.0.0",
          "checks": {
            "redis": {"status": "down", "error": "Redis connection failed"},
            "mongodb": {"status": "up", "latency_ms": 15},
            "jira": {"status": "up", "latency_ms": 90}
          }
        }

    Dependencies are injected from Flask application context (g object):
    - g.redis_client: Redis client instance
    - g.mongo_client: MongoDB client instance (None if disabled)
    - g.jira_client: Jira API client instance

    Returns:
        Flask JSON Response with health check results and appropriate status code

    Security:
    - No authentication required (designed for ALB health probes)
    - No sensitive information exposed in error messages
    - Generic error descriptions without stack traces

    Example ALB Configuration:
        Health check path: /healthz
        Health check interval: 30 seconds
        Health check timeout: 5 seconds
        Healthy threshold: 2 consecutive checks
        Unhealthy threshold: 3 consecutive checks
        Success codes: 200
    """
    # Import Flask g object for dependency injection
    from flask import g

    # Start overall health check timing
    overall_start = perf_counter()

    # Generate ISO 8601 timestamp for response
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Initialize response structure
    checks: Dict[str, Dict[str, Any]] = {}
    overall_status = "healthy"

    # Check Redis (required dependency)
    redis_status, redis_latency, redis_error = _check_redis(g.redis_client)
    checks["redis"] = {"status": redis_status}
    if redis_latency is not None:
        checks["redis"]["latency_ms"] = redis_latency
    if redis_error is not None:
        checks["redis"]["error"] = redis_error
        overall_status = "unhealthy"  # Required dependency failed

    # Check MongoDB (optional dependency)
    # g.mongo_client is None if ENABLE_MONGO=false
    mongo_status, mongo_latency, mongo_error = _check_mongodb(
        getattr(g, 'mongo_client', None)
    )
    checks["mongodb"] = {"status": mongo_status}
    if mongo_latency is not None:
        checks["mongodb"]["latency_ms"] = mongo_latency
    if mongo_error is not None:
        checks["mongodb"]["error"] = mongo_error
        # MongoDB is optional - don't change overall_status to unhealthy

    # Check Jira (required dependency)
    jira_status, jira_latency, jira_error = _check_jira(g.jira_client)
    checks["jira"] = {"status": jira_status}
    if jira_latency is not None:
        checks["jira"]["latency_ms"] = jira_latency
    if jira_error is not None:
        checks["jira"]["error"] = jira_error
        overall_status = "unhealthy"  # Required dependency failed

    # Build response body
    response_body: Dict[str, Any] = {
        "status": overall_status,
        "timestamp": timestamp,
        "version": APP_VERSION,
        "checks": checks
    }

    # Add optional resource metrics if available
    resource_metrics = _get_resource_metrics()
    if resource_metrics is not None:
        response_body["resources"] = resource_metrics

    # Calculate overall health check duration
    overall_duration_ms = round((perf_counter() - overall_start) * 1000, 2)

    # Determine HTTP status code based on required dependencies
    http_status_code = 200 if overall_status == "healthy" else 503

    # Log health check execution with structured fields
    logger.info(
        f"Health check completed: {overall_status}",
        extra={
            "action": "health_check_executed",
            "overall_status": overall_status,
            "redis_status": redis_status,
            "mongodb_status": mongo_status,
            "jira_status": jira_status,
            "duration_ms": overall_duration_ms,
            "http_status_code": http_status_code
        }
    )

    # Return JSON response with appropriate status code
    return jsonify(response_body), http_status_code
