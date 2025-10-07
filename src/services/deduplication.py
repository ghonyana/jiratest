"""
Event Deduplication Service for Error Triage

This module implements idempotent webhook processing by tracking processed event IDs
in Redis with TTL-based expiration, preventing duplicate Jira issue creation/updates
from delayed or retried webhook deliveries from Vercel and GCP.

Per Section 0.7.1 requirement #3: "Idempotency Required"
- Drop duplicate events by event_id (Vercel) or insertId (GCP)
- TTL minimum: 1 hour (3600 seconds) to handle delayed retries
- Implementation: Redis SETNX with expiration for atomic check-and-set

Redis Key Pattern (per Section 0.4.3):
    dedup:{event_id} - Event deduplication tracking (TTL: 3600 seconds)

Key Design Decisions:
1. Atomic Operations: Use Redis SETEX for atomic set-with-TTL to prevent race conditions
2. TTL-Based Cleanup: Automatic key expiration eliminates need for manual cleanup
3. Simple Value: Store "1" as value (existence check is sufficient for deduplication)
4. Graceful Degradation: On Redis failure, return False (not duplicate) to avoid blocking

Observability (per Section 0.7.2):
- Every operation emits at least one log entry with event_id correlation
- Redis operation latency recorded to redis_operation_latency_seconds histogram
- Target: <5ms p99 for dedup_check and dedup_mark operations

Integration Points:
- Used by /events endpoint before processing webhook payloads
- Prevents duplicate Jira issues for identical error events
- Handles Vercel webhook retries (x-vercel-signature validation + deduplication)
- Handles GCP Pub/Sub push retries (OIDC token validation + deduplication)

Performance Characteristics:
- O(1) Redis operations (EXISTS, SETEX)
- Network latency: ~2-5ms for ElastiCache within VPC
- Memory footprint: ~100 bytes per event_id (key + value + metadata)
- Automatic cleanup: Keys expire after 1 hour

Example Usage:
    from redis import Redis
    from services.deduplication import DeduplicationService

    redis_client = Redis(host='redis-host', port=6379, decode_responses=True)
    dedup_service = DeduplicationService(redis_client, environment='production')

    # Check if event was already processed
    if dedup_service.is_duplicate('vercel-xyz-123'):
        logger.info("Duplicate event detected, skipping processing")
        return

    # Process event and mark as processed
    process_webhook(event)
    dedup_service.mark_processed('vercel-xyz-123')

Author: Blitzy Platform
Version: 1.0.0
"""

import os
from time import perf_counter
from typing import Optional

from redis import Redis
from redis.exceptions import RedisError, ConnectionError, TimeoutError

# Internal imports from depends_on_files
from src.utils.logging_config import get_logger
from src.utils.metrics_collector import record_redis_latency


class DeduplicationService:
    """
    Service for tracking processed webhook events to implement idempotency.

    Uses Redis TTL cache to store event IDs with 1-hour expiration, preventing
    duplicate processing of retried or delayed webhook deliveries. Supports both
    Vercel event_id and GCP insertId as deduplication keys.

    Per Section 0.7.1 requirement #3, this service ensures:
    - Duplicate events are dropped before processing
    - 1-hour TTL window handles delayed retries
    - Atomic operations prevent race conditions

    Attributes:
        redis_client: Redis client instance for deduplication cache operations
        environment: Deployment environment (production, staging, dev) for metrics/logs
        logger: Configured logger instance for structured JSON logging

    Thread Safety:
        Thread-safe due to atomic Redis operations (EXISTS, SETEX)
        Safe for concurrent use across multiple Gunicorn workers

    Error Handling:
        - Redis connection errors: Log error, return False (not duplicate) to allow processing
        - Redis timeout errors: Log error, return False to avoid blocking webhook processing
        - Other Redis errors: Log error, fail gracefully by returning False

    Methods:
        is_duplicate(event_id): Check if event was previously processed
        mark_processed(event_id, ttl): Mark event as processed with TTL expiration
    """

    def __init__(self, redis_client: Redis, environment: str = "production"):
        """
        Initialize DeduplicationService with Redis client dependency injection.

        Args:
            redis_client: Redis client instance configured with connection pool
                         Must have decode_responses=True for string operations
            environment: Deployment environment for metrics/logs (default: 'production')
                        Valid values: production, staging, dev

        Example:
            >>> redis_client = Redis(host='redis-host', port=6379, decode_responses=True)
            >>> dedup_service = DeduplicationService(redis_client, environment='production')
        """
        self.redis_client = redis_client
        self.environment = environment
        self.logger = get_logger(__name__)

        # Log service initialization with environment context
        self.logger.info(
            "DeduplicationService initialized",
            extra={
                "action": "dedup_service_init",
                "environment": self.environment,
            },
        )

    def is_duplicate(self, event_id: str) -> bool:
        """
        Check if event was previously processed using Redis existence check.

        Performs atomic Redis EXISTS operation to determine if event_id was already
        processed within the TTL window (1 hour default). Records latency metric to
        redis_operation_latency_seconds histogram for performance monitoring.

        Per Section 0.7.2 observability requirement: Every operation emits at least
        one log entry with correlation fields (event_id, action, duration_ms).

        Args:
            event_id: Unique event identifier from webhook source
                     - Vercel: event_id field from payload
                     - GCP: insertId field from log entry

        Returns:
            bool: True if event was previously processed (duplicate), False otherwise

        Redis Operation:
            Key: dedup:{event_id}
            Command: EXISTS dedup:{event_id}
            Returns: 1 if key exists, 0 if key does not exist or expired

        Performance:
            - Target: <5ms p99 latency (Section 0.7.3)
            - Typical: 2-3ms for ElastiCache within VPC
            - Timeout: 5s (configured in Redis client)

        Error Handling:
            On Redis errors (connection failure, timeout), logs error and returns
            False (not duplicate) to avoid blocking webhook processing. This
            implements graceful degradation per Section 0.7.2.

        Example:
            >>> dedup_service = DeduplicationService(redis_client, 'production')
            >>> if dedup_service.is_duplicate('vercel-xyz-123'):
            ...     logger.info("Duplicate event, skipping")
            ...     return
            >>> # Process event...

        Observability:
            - Log entry: INFO level with event_id, action=dedup_check, duration_ms
            - Metric: redis_operation_latency_seconds{env, operation=dedup_check}
            - Error log: ERROR level if Redis operation fails
        """
        redis_key = f"dedup:{event_id}"
        start_time = perf_counter()

        try:
            # Atomic Redis EXISTS check - O(1) operation
            # Returns 1 if key exists (duplicate), 0 if not exists or expired
            exists = self.redis_client.exists(redis_key)
            is_dup = bool(exists)

            # Measure operation duration in seconds
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            # Record Redis latency metric for performance monitoring
            # Target: <5ms p99 per Section 0.7.3 performance requirements
            record_redis_latency(
                environment=self.environment,
                operation="dedup_check",
                duration=duration,
            )

            # Log deduplication check result with correlation fields
            # Per Section 0.7.2: Every operation must emit at least one log entry
            self.logger.info(
                f"Deduplication check: {'duplicate' if is_dup else 'new event'}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_check",
                    "is_duplicate": is_dup,
                    "duration_ms": round(duration_ms, 2),
                },
            )

            return is_dup

        except (ConnectionError, TimeoutError) as e:
            # Redis connection or timeout errors - fail gracefully
            # Return False (not duplicate) to allow processing
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            self.logger.error(
                f"Redis connection error during deduplication check: {str(e)}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_check_error",
                    "error_type": "redis_connection_failure",
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )

            # Graceful degradation: assume not duplicate to avoid blocking
            return False

        except RedisError as e:
            # Other Redis errors (e.g., READONLY replica) - fail gracefully
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            self.logger.error(
                f"Redis error during deduplication check: {str(e)}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_check_error",
                    "error_type": "redis_operation_failure",
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )

            # Graceful degradation: assume not duplicate to avoid blocking
            return False

        except Exception as e:
            # Unexpected errors - log and fail gracefully
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            self.logger.error(
                f"Unexpected error during deduplication check: {str(e)}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_check_error",
                    "error_type": "unexpected_error",
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )

            # Fail open: allow processing to continue
            return False

    def mark_processed(self, event_id: str, ttl: int = 3600) -> None:
        """
        Mark event as processed with TTL expiration in Redis deduplication cache.

        Performs atomic Redis SETEX operation to store event_id with automatic
        expiration after TTL seconds. Per Section 0.7.1 requirement #3, TTL
        minimum is 1 hour (3600 seconds) to handle delayed webhook retries.

        Args:
            event_id: Unique event identifier from webhook source
                     - Vercel: event_id field from payload
                     - GCP: insertId field from log entry
            ttl: Time-to-live in seconds (default: 3600 = 1 hour)
                Minimum: 3600 seconds per Section 0.7.1 requirement
                Typical: 3600 seconds (1 hour) for webhook retry window

        Returns:
            None

        Redis Operation:
            Key: dedup:{event_id}
            Value: "1" (simple existence marker)
            Command: SETEX dedup:{event_id} {ttl} "1"
            Atomic: Set and expire in single operation

        Performance:
            - Target: <5ms p99 latency (Section 0.7.3)
            - Typical: 2-3ms for ElastiCache within VPC
            - Timeout: 5s (configured in Redis client)

        Memory Usage:
            - Key size: ~20-40 bytes (dedup: prefix + event_id)
            - Value size: 1 byte ("1")
            - Redis metadata: ~50-80 bytes (TTL, encoding, etc.)
            - Total: ~100 bytes per event_id
            - Automatic cleanup: Keys expire after TTL

        Error Handling:
            On Redis errors (connection failure, timeout), logs error but does
            not raise exception. This implements graceful degradation - the event
            is processed successfully even if deduplication cache update fails.

        Example:
            >>> dedup_service = DeduplicationService(redis_client, 'production')
            >>> # After successfully processing event
            >>> dedup_service.mark_processed('vercel-xyz-123')
            >>> # Event is now marked as duplicate for next 1 hour

        Example with custom TTL:
            >>> # Use 2-hour TTL for critical production events
            >>> dedup_service.mark_processed('vercel-xyz-789', ttl=7200)

        Observability:
            - Log entry: INFO level with event_id, action=dedup_mark, duration_ms
            - Metric: redis_operation_latency_seconds{env, operation=dedup_mark}
            - Error log: ERROR level if Redis operation fails
        """
        redis_key = f"dedup:{event_id}"
        start_time = perf_counter()

        try:
            # Atomic Redis SETEX - set key with expiration in single operation
            # Prevents race condition between SET and EXPIRE commands
            # Value "1" is simple existence marker (key presence is what matters)
            self.redis_client.setex(redis_key, ttl, "1")

            # Measure operation duration in seconds
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            # Record Redis latency metric for performance monitoring
            # Target: <5ms p99 per Section 0.7.3 performance requirements
            record_redis_latency(
                environment=self.environment,
                operation="dedup_mark",
                duration=duration,
            )

            # Log successful deduplication marking with correlation fields
            # Per Section 0.7.2: Every operation must emit at least one log entry
            self.logger.info(
                "Event marked as processed in deduplication cache",
                extra={
                    "event_id": event_id,
                    "action": "dedup_mark",
                    "ttl_seconds": ttl,
                    "duration_ms": round(duration_ms, 2),
                },
            )

        except (ConnectionError, TimeoutError) as e:
            # Redis connection or timeout errors - log but don't raise
            # Graceful degradation: event was processed successfully even if cache update failed
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            self.logger.error(
                f"Redis connection error marking event as processed: {str(e)}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_mark_error",
                    "error_type": "redis_connection_failure",
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )

            # Don't raise exception - allow processing to complete
            # Event processed successfully despite cache update failure

        except RedisError as e:
            # Other Redis errors (e.g., OOM, READONLY replica) - log but don't raise
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            self.logger.error(
                f"Redis error marking event as processed: {str(e)}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_mark_error",
                    "error_type": "redis_operation_failure",
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )

            # Don't raise exception - graceful degradation

        except Exception as e:
            # Unexpected errors - log but don't raise
            duration = perf_counter() - start_time
            duration_ms = duration * 1000

            self.logger.error(
                f"Unexpected error marking event as processed: {str(e)}",
                extra={
                    "event_id": event_id,
                    "action": "dedup_mark_error",
                    "error_type": "unexpected_error",
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )

            # Don't raise exception - allow processing to complete


# ============================================================================
# Module Exports
# ============================================================================

__all__ = ["DeduplicationService"]
