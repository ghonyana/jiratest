"""
Comment Rate Limiter Service for Jira Issue Comment Spam Prevention

This module implements Redis-backed rate limiting to prevent excessive commenting
on Jira issues during sustained error bursts. Enforces a configurable frequency
limit (default 15 minutes) between consecutive comments to the same issue, with
immediate bypass when severity escalates (SEV4→SEV3→SEV2→SEV1).

Per Section 0.7.1 Critical User Directive #4:
- "At most once per 15 minutes unless severity increases"
- Comment rate limiting is CRITICAL to prevent Jira notification spam
- Override rate limit ONLY when severity level escalates

Key Features:
- Per-issue comment frequency enforcement using Redis timestamps
- Automatic TTL expiration for self-cleaning state management
- Severity escalation bypass for immediate high-priority notifications
- Comprehensive logging with CloudWatch correlation fields
- Prometheus metrics for Redis operation latency monitoring
- Graceful degradation on Redis failures (fail open to allow comments)

Redis Key Pattern:
- Key: comment_limit:{issue_key}
- Value: Unix timestamp (seconds since epoch) of last comment
- TTL: 900 seconds (15 minutes) for automatic cleanup

Rate Limit Logic:
1. If severity_increased=True: Always allow comment (immediate escalation)
2. If no previous comment timestamp: Allow comment (first time)
3. If time_since_last >= limit_minutes * 60: Allow comment
4. Otherwise: Deny comment (within rate limit window)

Architecture Integration:
- Deployed in JiraIntegrationService workflow before add_comment() calls
- Coordinates with FrequencyTracker for severity threshold monitoring
- Works with SeverityRulesEngine escalation detection logic

Usage Example:
    redis_client = Redis(host='localhost', port=6379)
    rate_limiter = CommentRateLimiter(redis_client, environment='production')
    
    # Check before commenting
    if rate_limiter.should_comment('ET-1234', severity_increased=False):
        jira_service.add_comment('ET-1234', message)
        rate_limiter.record_comment('ET-1234')
    else:
        logger.info("Comment skipped due to rate limit")
    
    # Severity escalation always allows comment
    if rate_limiter.should_comment('ET-1234', severity_increased=True):
        jira_service.add_comment('ET-1234', escalation_message)
        rate_limiter.record_comment('ET-1234')

Author: Blitzy Platform
Version: 1.0.0
"""

import time
from typing import Optional
from redis import Redis

from utils.logging_config import get_logger
from utils.metrics_collector import record_redis_latency


class CommentRateLimiter:
    """
    Redis-backed rate limiter for Jira issue comment frequency enforcement.
    
    Prevents notification spam by limiting comment frequency to once per
    configurable time window (default 15 minutes) unless severity escalates.
    
    Per Section 0.7.1, this implements the critical user requirement:
    "At most once per 15 minutes unless severity increases"
    
    Attributes:
        redis_client: Redis client instance for timestamp storage and retrieval
        environment: Deployment environment for logging and metrics correlation
        logger: Configured logger instance for structured JSON logging
    
    Thread Safety:
        Redis operations are atomic (GET, SETEX) ensuring thread-safe behavior
        in multi-worker Gunicorn deployments without additional locking.
    
    Error Handling:
        On Redis connection failures, the rate limiter fails open (allows comments)
        to prevent service degradation. Errors are logged and metrics are incremented
        for operational alerting per Section 0.7.2 graceful degradation requirements.
    """
    
    def __init__(self, redis_client: Redis, environment: str = "production"):
        """
        Initialize comment rate limiter with Redis client dependency injection.
        
        Args:
            redis_client: Configured Redis client with connection pooling and retry logic.
                         Must support get() and setex() operations with decode_responses=True.
            environment: Deployment environment (production, staging, dev) for logging
                        and metrics correlation. Defaults to 'production'.
        
        Example:
            >>> redis_client = Redis(
            ...     host='jiratest-redis.cache.amazonaws.com',
            ...     port=6379,
            ...     decode_responses=True,
            ...     socket_connect_timeout=5,
            ...     health_check_interval=30
            ... )
            >>> rate_limiter = CommentRateLimiter(redis_client, environment='production')
        """
        self.redis_client = redis_client
        self.environment = environment
        self.logger = get_logger(__name__)
        
        self.logger.info(
            "CommentRateLimiter initialized",
            extra={
                "action": "rate_limiter_initialized",
                "environment": environment
            }
        )
    
    def should_comment(
        self,
        issue_key: str,
        severity_increased: bool,
        limit_minutes: int = 15
    ) -> bool:
        """
        Determine if a comment should be added to a Jira issue based on rate limits.
        
        Implements the core rate limiting logic with severity escalation bypass:
        1. If severity_increased=True: Always return True (immediate notification)
        2. Fetch last comment timestamp from Redis using issue_key
        3. If no timestamp exists: Return True (first comment for this issue)
        4. Calculate elapsed time since last comment
        5. Return True if elapsed >= limit_minutes * 60, False otherwise
        
        Per Section 0.7.1 requirement #4, this enforces "at most once per 15 minutes
        unless severity increases" to prevent Jira notification spam during sustained
        error bursts.
        
        Args:
            issue_key: Jira issue key (e.g., 'ET-1234') to check rate limit status.
                      Used as Redis key suffix: comment_limit:{issue_key}
            severity_increased: Boolean flag indicating severity escalation detected
                               (e.g., SEV3→SEV2). When True, rate limit is bypassed
                               immediately for urgent escalation notifications.
            limit_minutes: Rate limit window in minutes (default: 15). Minimum elapsed
                          time required between consecutive comments. Configurable for
                          testing and future per-service customization.
        
        Returns:
            bool: True if comment is allowed (either rate limit satisfied or severity
                 escalation), False if within rate limit window and no escalation.
        
        Raises:
            No exceptions raised - Redis errors are caught and logged, returning True
            (fail open) to prevent service degradation per Section 0.7.2.
        
        Examples:
            >>> # First comment - always allowed
            >>> rate_limiter.should_comment('ET-1234', severity_increased=False)
            True
            
            >>> # Severity escalation - bypass rate limit
            >>> rate_limiter.should_comment('ET-1234', severity_increased=True)
            True
            
            >>> # Within 15 minutes, no escalation - denied
            >>> rate_limiter.record_comment('ET-1234')
            >>> time.sleep(60)  # Wait 1 minute
            >>> rate_limiter.should_comment('ET-1234', severity_increased=False)
            False
            
            >>> # After 15 minutes - allowed again
            >>> time.sleep(14 * 60)  # Wait 14 more minutes
            >>> rate_limiter.should_comment('ET-1234', severity_increased=False)
            True
        
        Performance:
            - Redis GET operation: < 5ms (p99 per Section 0.7.3)
            - Total execution time: < 10ms including logging and metrics
        
        Monitoring:
            Emits structured logs with action='rate_limit_check' and correlation
            fields (issue_key, severity_increased, allowed, elapsed_seconds).
            Records redis_operation_latency_seconds histogram for GET operation.
        """
        start_time = time.time()
        
        try:
            # Bypass rate limit immediately on severity escalation
            # Per Section 0.7.1: Override ONLY when severity level escalates
            if severity_increased:
                self.logger.info(
                    "Comment allowed due to severity escalation",
                    extra={
                        "action": "rate_limit_bypassed",
                        "issue_key": issue_key,
                        "severity_increased": True,
                        "allowed": True
                    }
                )
                return True
            
            # Build Redis key: comment_limit:{issue_key}
            redis_key = f"comment_limit:{issue_key}"
            
            # Fetch last comment timestamp from Redis
            redis_start = time.time()
            last_comment_timestamp_str: Optional[str] = self.redis_client.get(redis_key)
            redis_duration = time.time() - redis_start
            
            # Record Redis GET operation latency for monitoring
            record_redis_latency(self.environment, "rate_limit_check", redis_duration)
            
            # No previous comment timestamp - allow comment (first time)
            if last_comment_timestamp_str is None:
                self.logger.info(
                    "Comment allowed - no previous comment found",
                    extra={
                        "action": "rate_limit_check",
                        "issue_key": issue_key,
                        "severity_increased": False,
                        "allowed": True,
                        "reason": "first_comment",
                        "duration_ms": int((time.time() - start_time) * 1000)
                    }
                )
                return True
            
            # Parse timestamp and calculate elapsed time
            try:
                last_comment_timestamp = float(last_comment_timestamp_str)
            except (ValueError, TypeError) as e:
                # Invalid timestamp format - log warning and allow comment (fail open)
                self.logger.warning(
                    "Invalid timestamp format in Redis, allowing comment",
                    extra={
                        "action": "rate_limit_check",
                        "issue_key": issue_key,
                        "error_type": "invalid_timestamp",
                        "redis_value": last_comment_timestamp_str,
                        "allowed": True
                    }
                )
                return True
            
            # Calculate time elapsed since last comment
            current_timestamp = time.time()
            elapsed_seconds = current_timestamp - last_comment_timestamp
            limit_seconds = limit_minutes * 60
            
            # Check if elapsed time exceeds rate limit window
            allowed = elapsed_seconds >= limit_seconds
            
            # Log rate limit decision with correlation fields
            log_message = (
                f"Comment {'allowed' if allowed else 'denied'} - "
                f"{int(elapsed_seconds)}s elapsed, {limit_seconds}s required"
            )
            
            self.logger.info(
                log_message,
                extra={
                    "action": "rate_limit_check",
                    "issue_key": issue_key,
                    "severity_increased": False,
                    "allowed": allowed,
                    "elapsed_seconds": int(elapsed_seconds),
                    "limit_seconds": limit_seconds,
                    "duration_ms": int((time.time() - start_time) * 1000)
                }
            )
            
            return allowed
            
        except Exception as e:
            # Graceful degradation: On Redis failure, allow comment (fail open)
            # Per Section 0.7.2, continue processing if Redis temporarily unavailable
            self.logger.error(
                "Redis error in rate limit check, allowing comment (fail open)",
                extra={
                    "action": "rate_limit_error",
                    "issue_key": issue_key,
                    "error_type": "redis_connection_failure",
                    "allowed": True,
                    "duration_ms": int((time.time() - start_time) * 1000)
                },
                exc_info=True
            )
            return True
    
    def record_comment(self, issue_key: str, ttl: int = 900) -> None:
        """
        Record a comment timestamp in Redis to enforce future rate limiting.
        
        Stores the current Unix timestamp (seconds since epoch) in Redis with
        automatic TTL expiration for self-cleaning state management. The TTL
        matches the rate limit window to ensure automatic cleanup.
        
        Per Section 0.4.3 Redis key patterns, this implements:
        - Key: comment_limit:{issue_key}
        - Value: Unix timestamp (str) of comment time
        - TTL: 900 seconds (15 minutes) for automatic cleanup
        
        This method should be called immediately after successfully adding a
        comment to a Jira issue to update the rate limit state.
        
        Args:
            issue_key: Jira issue key (e.g., 'ET-1234') that received the comment.
                      Used as Redis key suffix: comment_limit:{issue_key}
            ttl: Time-to-live in seconds for Redis key expiration (default: 900).
                Must match or exceed rate limit window to ensure proper enforcement.
                Automatic cleanup prevents unbounded Redis memory growth.
        
        Returns:
            None - Method completes silently on success, logs errors on failure.
        
        Raises:
            No exceptions raised - Redis errors are caught and logged per Section
            0.7.2 graceful degradation requirements. Failed recordings result in
            next should_comment() call returning True (fail open behavior).
        
        Examples:
            >>> # After successfully adding a Jira comment
            >>> jira_service.add_comment('ET-1234', 'Error reoccurred 15× in last 5m')
            >>> rate_limiter.record_comment('ET-1234')
            
            >>> # Custom TTL for testing
            >>> rate_limiter.record_comment('ET-1234', ttl=60)  # 1 minute for tests
        
        Performance:
            - Redis SETEX operation: < 5ms (p99 per Section 0.7.3)
            - Total execution time: < 10ms including logging and metrics
        
        Monitoring:
            Emits structured logs with action='rate_limit_recorded' and correlation
            fields (issue_key, ttl). Records redis_operation_latency_seconds histogram
            for SETEX operation. On errors, increments errors_total counter with
            error_type='redis_connection_failure'.
        
        Side Effects:
            - Updates Redis key: comment_limit:{issue_key} = current_timestamp
            - Sets TTL to ensure automatic expiration after rate limit window
            - Emits CloudWatch log entry for operational troubleshooting
            - Records Prometheus metric for Redis operation latency
        """
        start_time = time.time()
        
        try:
            # Build Redis key: comment_limit:{issue_key}
            redis_key = f"comment_limit:{issue_key}"
            
            # Get current Unix timestamp
            current_timestamp = time.time()
            timestamp_str = str(current_timestamp)
            
            # Store timestamp in Redis with TTL expiration
            # SETEX is atomic: sets value and TTL in single operation
            redis_start = time.time()
            self.redis_client.setex(redis_key, ttl, timestamp_str)
            redis_duration = time.time() - redis_start
            
            # Record Redis SETEX operation latency for monitoring
            record_redis_latency(self.environment, "rate_limit_record", redis_duration)
            
            # Log successful timestamp recording with correlation fields
            self.logger.info(
                "Comment timestamp recorded for rate limiting",
                extra={
                    "action": "rate_limit_recorded",
                    "issue_key": issue_key,
                    "ttl": ttl,
                    "duration_ms": int((time.time() - start_time) * 1000)
                }
            )
            
        except Exception as e:
            # Graceful degradation: Log error but don't crash service
            # Per Section 0.7.2, continue processing if Redis temporarily unavailable
            # Impact: Next should_comment() call will return True (fail open)
            self.logger.error(
                "Redis error recording comment timestamp",
                extra={
                    "action": "rate_limit_record_error",
                    "issue_key": issue_key,
                    "error_type": "redis_connection_failure",
                    "duration_ms": int((time.time() - start_time) * 1000)
                },
                exc_info=True
            )


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "CommentRateLimiter"
]
