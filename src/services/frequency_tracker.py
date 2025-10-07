"""
Redis-based Frequency Counter Management Service

This service maintains rolling 5-minute occurrence counters per (environment, fingerprint)
pair for error frequency tracking and severity classification. Counters are implemented
using atomic Redis INCR operations with TTL expiration to provide real-time error
frequency data while automatically expiring stale counters.

Key Features:
- Atomic counter operations using Redis INCR with TTL for race condition prevention
- Rolling 5-minute windows via 300-second TTL on counter keys
- Graceful degradation: Falls back to count=1 when Redis unavailable
- Connection pooling for high performance (< 5ms p99 SLO target)
- Comprehensive observability: Structured logging and Prometheus metrics

Redis Key Pattern:
    freq:{environment}:{fingerprint}

    Examples:
    - freq:production:a3f5b9c8d2e1f4g6h8j9k0 -> 15
    - freq:staging:x7y9z1a2b3c4d5e6f7g8h9 -> 3

Architecture:
    FrequencyTracker uses dependency injection for the Redis client, enabling:
    - Unit testing with mock Redis (fakeredis)
    - Integration testing with real Redis (docker-compose)
    - Production deployment with ElastiCache connection pooling

Per Section 0.1.1 requirement #3:
    Frequency-based severity classification requires accurate 5-minute rolling
    counters for each (environment, fingerprint) pair. Redis TTL ensures automatic
    cleanup without background jobs.

Per Section 0.7.2 graceful degradation pattern:
    When Redis is unavailable, the service falls back to count=1 (always create
    or comment) to maintain service availability, logs degraded mode warning,
    and resumes normal operation when Redis recovers.

Per Section 0.7.3 performance requirements:
    Redis operations must complete in < 5ms (p99) to meet < 200ms p95 webhook
    processing SLO. Connection pooling and pipeline operations optimize latency.

Usage Example:
    from redis import Redis
    from services.frequency_tracker import FrequencyTracker

    # Initialize with Redis client
    redis_client = Redis(host='localhost', port=6379, decode_responses=True)
    tracker = FrequencyTracker(redis_client=redis_client, environment='production')

    # Increment counter for error occurrence
    count = tracker.increment(env='production', fingerprint='a3f5b9...')
    # Returns: 15 (current count in 5-minute window)

    # Retrieve current count without incrementing
    current_count = tracker.get_count(env='production', fingerprint='a3f5b9...')
    # Returns: 15 (or 0 if key expired/missing)

Author: Blitzy Platform
Version: 1.0.0
"""

import os
from time import perf_counter
from typing import Optional

from redis import Redis
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

from utils.logging_config import get_logger
from utils.metrics_collector import record_redis_latency, increment_error


# Initialize module logger
logger = get_logger(__name__)


class FrequencyTracker:
    """
    Redis-based frequency counter manager for rolling 5-minute error occurrence tracking.

    Maintains atomic counters per (environment, fingerprint) pair with automatic TTL
    expiration for rolling window implementation. Provides graceful degradation when
    Redis is unavailable to maintain service availability.

    Attributes:
        redis_client: Injected Redis client instance with connection pooling
        environment: Deployment environment (production, staging, dev) for metrics
        default_ttl: Default TTL for counter keys in seconds (default: 300 = 5 minutes)

    Thread Safety:
        Redis INCR and EXPIRE operations are atomic. Multiple workers can safely
        increment the same counter concurrently without race conditions.

    Performance:
        - Uses Redis pipeline for atomic INCR + EXPIRE (single round-trip)
        - Connection pooling reduces connection overhead
        - Target: < 5ms p99 latency per Section 0.7.3

    Error Handling:
        - Redis connection failures: Log error, fallback to count=1, emit metric
        - Redis timeout: Log warning, fallback to count=1, emit metric
        - Network errors: Log error, fallback to count=1, emit metric
        - Service continues processing despite Redis unavailability

    Example:
        >>> from redis import Redis
        >>> redis_client = Redis(host='localhost', port=6379, decode_responses=True)
        >>> tracker = FrequencyTracker(redis_client=redis_client, environment='production')
        >>> 
        >>> # First occurrence in 5-minute window
        >>> count = tracker.increment('production', 'a3f5b9...')
        >>> print(count)  # Output: 1
        >>> 
        >>> # Subsequent occurrences within 5 minutes
        >>> count = tracker.increment('production', 'a3f5b9...')
        >>> print(count)  # Output: 2
        >>> 
        >>> # Retrieve without incrementing
        >>> current = tracker.get_count('production', 'a3f5b9...')
        >>> print(current)  # Output: 2
    """

    def __init__(self, redis_client: Redis, environment: str = "production", default_ttl: int = 300):
        """
        Initialize FrequencyTracker with injected Redis client.

        Args:
            redis_client: Redis client instance with connection pooling configured.
                         Must have decode_responses=True for string key/value handling.
            environment: Deployment environment for metric labeling and logging.
                        Valid values: production, staging, dev
            default_ttl: Default TTL for counter keys in seconds.
                        Default: 300 (5 minutes per Section 0.4.3)

        Raises:
            ValueError: If environment is invalid or default_ttl is non-positive

        Example:
            >>> from redis import Redis
            >>> redis_client = Redis(
            ...     host='redis.example.com',
            ...     port=6379,
            ...     decode_responses=True,
            ...     socket_connect_timeout=5,
            ...     socket_timeout=5,
            ...     retry_on_timeout=True,
            ...     health_check_interval=30
            ... )
            >>> tracker = FrequencyTracker(
            ...     redis_client=redis_client,
            ...     environment='production',
            ...     default_ttl=300
            ... )
        """
        if not redis_client:
            raise ValueError("redis_client is required")

        valid_environments = {"production", "staging", "dev"}
        if environment not in valid_environments:
            raise ValueError(f"Invalid environment: {environment}. Must be one of {valid_environments}")

        if default_ttl <= 0:
            raise ValueError(f"default_ttl must be positive, got {default_ttl}")

        self.redis_client = redis_client
        self.environment = environment
        self.default_ttl = default_ttl

        logger.info(
            "FrequencyTracker initialized",
            extra={
                "action": "frequency_tracker_initialized",
                "environment": environment,
                "default_ttl": default_ttl,
            },
        )

    def increment(self, env: str, fingerprint: str, ttl: Optional[int] = None) -> int:
        """
        Atomically increment frequency counter for (environment, fingerprint) pair.

        Generates Redis key using pattern freq:{env}:{fingerprint}, atomically
        increments the counter, and sets TTL for rolling window expiration.
        Uses Redis pipeline to execute INCR and EXPIRE in a single round-trip
        for optimal performance.

        Per Section 0.5.1 Group 3, this method:
        - Generates key: f'freq:{env}:{fingerprint}'
        - Executes: pipeline.incr(key).expire(key, ttl).execute()
        - Returns: Current count after increment

        Per Section 0.7.2 graceful degradation:
        - If Redis unavailable: Return count=1 (fallback)
        - Log degraded mode warning with correlation fields
        - Emit redis_connection_failure metric
        - Service continues processing without crashing

        Args:
            env: Environment name (production, staging, dev) for key namespace
            fingerprint: SHA-256 error fingerprint hash (40-64 hex characters)
            ttl: Time-to-live in seconds (default: self.default_ttl = 300)

        Returns:
            int: Current count after increment (1 for first occurrence in window).
                 Returns 1 as fallback when Redis is unavailable.

        Raises:
            ValueError: If env or fingerprint is empty/None

        Example - Normal operation:
            >>> count = tracker.increment('production', 'a3f5b9c8d2e1f4g6h8j9k0')
            >>> print(count)  # Output: 1 (first occurrence)
            >>> 
            >>> count = tracker.increment('production', 'a3f5b9c8d2e1f4g6h8j9k0')
            >>> print(count)  # Output: 2 (second occurrence within 5 minutes)

        Example - Redis unavailable (graceful degradation):
            >>> # Redis connection fails
            >>> count = tracker.increment('production', 'a3f5b9c8d2e1f4g6h8j9k0')
            >>> print(count)  # Output: 1 (fallback count)
            >>> # Service continues processing, logs warning, emits metric

        Example - Custom TTL:
            >>> # Use 10-minute window instead of default 5 minutes
            >>> count = tracker.increment('production', 'a3f5b9...', ttl=600)
        """
        # Input validation
        if not env or not isinstance(env, str):
            raise ValueError("env must be a non-empty string")

        if not fingerprint or not isinstance(fingerprint, str):
            raise ValueError("fingerprint must be a non-empty string")

        # Use default TTL if not specified
        ttl_seconds = ttl if ttl is not None else self.default_ttl

        # Generate Redis key using pattern from Section 0.4.3
        key = f"freq:{env}:{fingerprint}"

        # Start performance timer for metrics
        start_time = perf_counter()

        try:
            # Use Redis pipeline for atomic INCR + EXPIRE in single round-trip
            # This prevents race condition between increment and TTL setting
            pipeline = self.redis_client.pipeline()
            pipeline.incr(key)
            pipeline.expire(key, ttl_seconds)
            results = pipeline.execute()

            # Extract count from pipeline results
            # results[0] = INCR return value (new count)
            # results[1] = EXPIRE return value (1 if successful)
            count = int(results[0])

            # Record successful operation latency
            duration = perf_counter() - start_time
            record_redis_latency(self.environment, "frequency_incr", duration)

            # Log successful increment with correlation fields
            logger.debug(
                "Incremented frequency counter",
                extra={
                    "action": "redis_frequency_incr",
                    "fingerprint": fingerprint,
                    "env": env,
                    "count": count,
                    "ttl": ttl_seconds,
                    "duration_ms": int(duration * 1000),
                },
            )

            return count

        except RedisConnectionError as e:
            # Redis connection failed - graceful degradation per Section 0.7.2
            duration = perf_counter() - start_time

            logger.warning(
                "Redis connection failed during frequency increment, falling back to count=1",
                extra={
                    "action": "redis_connection_failure",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "redis_connection_failure",
                    "duration_ms": int(duration * 1000),
                },
            )

            # Emit error metric for alerting
            increment_error(self.environment, "redis_connection_failure")

            # Return fallback count per Section 0.7.2
            return 1

        except RedisError as e:
            # Other Redis errors (timeout, command error, etc.)
            duration = perf_counter() - start_time

            logger.warning(
                f"Redis error during frequency increment: {type(e).__name__}, falling back to count=1",
                extra={
                    "action": "redis_error",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "redis_operation_error",
                    "duration_ms": int(duration * 1000),
                },
            )

            # Emit error metric
            increment_error(self.environment, "redis_connection_failure")

            # Return fallback count
            return 1

        except Exception as e:
            # Unexpected errors - log and fallback
            duration = perf_counter() - start_time

            logger.error(
                f"Unexpected error during frequency increment: {type(e).__name__}, falling back to count=1",
                extra={
                    "action": "frequency_incr_error",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "unexpected_error",
                    "duration_ms": int(duration * 1000),
                },
                exc_info=True,
            )

            # Emit error metric
            increment_error(self.environment, "redis_connection_failure")

            # Return fallback count to maintain service availability
            return 1

    def get_count(self, env: str, fingerprint: str) -> int:
        """
        Retrieve current frequency counter value without incrementing.

        Fetches the current count for the (environment, fingerprint) pair from
        Redis. Returns 0 if the key has expired or does not exist (outside the
        5-minute rolling window).

        Per Section 0.5.1 Group 3, this method:
        - Fetches value: redis.get(f'freq:{env}:{fingerprint}')
        - Returns: Current count (int) or 0 if key expired/missing

        This method is used for:
        - Severity rule evaluation before incrementing
        - Diagnostic queries and monitoring
        - Testing and verification

        Args:
            env: Environment name (production, staging, dev) for key namespace
            fingerprint: SHA-256 error fingerprint hash

        Returns:
            int: Current count in 5-minute window, or 0 if expired/missing.
                 Returns 0 as fallback when Redis is unavailable.

        Raises:
            ValueError: If env or fingerprint is empty/None

        Example - Key exists:
            >>> count = tracker.get_count('production', 'a3f5b9c8d2e1f4g6h8j9k0')
            >>> print(count)  # Output: 15 (current count)

        Example - Key expired or never created:
            >>> count = tracker.get_count('production', 'nonexistent-fingerprint')
            >>> print(count)  # Output: 0

        Example - Redis unavailable:
            >>> # Redis connection fails
            >>> count = tracker.get_count('production', 'a3f5b9c8d2e1f4g6h8j9k0')
            >>> print(count)  # Output: 0 (fallback)
        """
        # Input validation
        if not env or not isinstance(env, str):
            raise ValueError("env must be a non-empty string")

        if not fingerprint or not isinstance(fingerprint, str):
            raise ValueError("fingerprint must be a non-empty string")

        # Generate Redis key using pattern from Section 0.4.3
        key = f"freq:{env}:{fingerprint}"

        # Start performance timer for metrics
        start_time = perf_counter()

        try:
            # Fetch value from Redis
            value = self.redis_client.get(key)

            # Record successful operation latency
            duration = perf_counter() - start_time
            record_redis_latency(self.environment, "frequency_get", duration)

            # Convert to int, return 0 if None (key doesn't exist or expired)
            count = int(value) if value is not None else 0

            # Log successful retrieval
            logger.debug(
                "Retrieved frequency counter",
                extra={
                    "action": "redis_frequency_get",
                    "fingerprint": fingerprint,
                    "env": env,
                    "count": count,
                    "duration_ms": int(duration * 1000),
                },
            )

            return count

        except RedisConnectionError as e:
            # Redis connection failed - graceful degradation
            duration = perf_counter() - start_time

            logger.warning(
                "Redis connection failed during frequency retrieval, returning 0",
                extra={
                    "action": "redis_connection_failure",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "redis_connection_failure",
                    "duration_ms": int(duration * 1000),
                },
            )

            # Emit error metric
            increment_error(self.environment, "redis_connection_failure")

            # Return 0 as fallback (treat as expired/missing)
            return 0

        except RedisError as e:
            # Other Redis errors
            duration = perf_counter() - start_time

            logger.warning(
                f"Redis error during frequency retrieval: {type(e).__name__}, returning 0",
                extra={
                    "action": "redis_error",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "redis_operation_error",
                    "duration_ms": int(duration * 1000),
                },
            )

            # Emit error metric
            increment_error(self.environment, "redis_connection_failure")

            # Return 0 as fallback
            return 0

        except (ValueError, TypeError) as e:
            # Value conversion error (Redis returned non-numeric value)
            duration = perf_counter() - start_time

            logger.error(
                f"Invalid counter value in Redis: {type(e).__name__}, returning 0",
                extra={
                    "action": "redis_value_error",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "redis_value_error",
                    "duration_ms": int(duration * 1000),
                },
                exc_info=True,
            )

            # Return 0 for invalid data
            return 0

        except Exception as e:
            # Unexpected errors
            duration = perf_counter() - start_time

            logger.error(
                f"Unexpected error during frequency retrieval: {type(e).__name__}, returning 0",
                extra={
                    "action": "frequency_get_error",
                    "fingerprint": fingerprint,
                    "env": env,
                    "error_type": "unexpected_error",
                    "duration_ms": int(duration * 1000),
                },
                exc_info=True,
            )

            # Emit error metric
            increment_error(self.environment, "redis_connection_failure")

            # Return 0 as fallback
            return 0


# ============================================================================
# Module Exports
# ============================================================================

__all__ = ["FrequencyTracker"]
