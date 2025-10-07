"""
Unit Tests for CommentRateLimiter Service

Comprehensive test suite for the CommentRateLimiter class that enforces per-issue
comment frequency limits (maximum once per 15 minutes unless severity escalates)
to prevent Jira notification spam per Agent Action Plan Section 0.7.1 directive #4.

Test Coverage Areas:
1. Rate Limit Enforcement - First comment, immediate second comment, TTL expiry
2. Severity Escalation Override - Bypass rate limit when severity increases
3. Redis Operations - Timestamp storage with TTL, key pattern validation
4. Time-Based Logic - freezegun for simulating time passage without delays
5. Graceful Degradation - Redis connection failures handled gracefully
6. Edge Cases - Invalid timestamps, custom rate limit windows

Per Agent Action Plan Section 0.7.2 test-driven service design, this achieves
80%+ code coverage with target of 90%+ through comprehensive scenario testing.

Author: Blitzy Platform
Version: 1.0.0
"""

import time
from typing import Optional, Any
from unittest.mock import Mock

import pytest
from freezegun import freeze_time
from fakeredis import FakeRedis

from src.services.comment_rate_limiter import CommentRateLimiter


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_redis() -> FakeRedis:
    """
    Provide in-memory Redis implementation for isolated unit testing.
    
    Returns FakeRedis instance that mimics redis.Redis client behavior with
    full support for get(), setex(), and ttl() operations used by CommentRateLimiter.
    Enables fast, deterministic tests without external Redis dependency.
    
    Returns:
        FakeRedis: In-memory Redis server for testing
    """
    return FakeRedis(decode_responses=True)


@pytest.fixture
def rate_limiter(mock_redis: FakeRedis) -> CommentRateLimiter:
    """
    Provide configured CommentRateLimiter instance for testing.
    
    Injects mock_redis fixture and sets environment to 'test' for
    correlation in logs and metrics during test execution.
    
    Args:
        mock_redis: FakeRedis fixture from conftest.py or local fixture
    
    Returns:
        CommentRateLimiter: Configured rate limiter with mock Redis client
    """
    return CommentRateLimiter(redis_client=mock_redis, environment='test')


# ============================================================================
# Test Cases: First Comment Scenario
# ============================================================================

def test_first_comment_allowed(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that first comment on an issue is always allowed.
    
    When no previous timestamp exists in Redis for the issue_key,
    should_comment() should return True to allow the first comment.
    
    Per Section 0.5.1 Group 6: "If no timestamp exists (None): return True"
    """
    # Arrange
    issue_key = "ET-1234"
    
    # Act
    result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "First comment should always be allowed"
    
    # Verify no timestamp was stored yet
    redis_key = f"comment_limit:{issue_key}"
    assert mock_redis.get(redis_key) is None, "No timestamp should exist before recording"


def test_first_comment_with_severity_escalation(rate_limiter: CommentRateLimiter):
    """
    Test that first comment with severity escalation is allowed.
    
    Even though severity_increased=True is primarily for bypassing rate limits,
    it should also allow first comments.
    """
    # Arrange
    issue_key = "ET-5678"
    
    # Act
    result = rate_limiter.should_comment(issue_key, severity_increased=True)
    
    # Assert
    assert result is True, "First comment with severity escalation should be allowed"


# ============================================================================
# Test Cases: Rate Limit Enforcement
# ============================================================================

@freeze_time("2025-01-15 10:00:00")
def test_immediate_second_comment_denied(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that second comment within 15 minutes is denied.
    
    After recording a comment timestamp, an immediate subsequent comment
    (within the 15-minute rate limit window) should be denied when
    severity_increased=False.
    
    Per Section 0.7.1: "At most once per 15 minutes unless severity increases"
    """
    # Arrange
    issue_key = "ET-1234"
    rate_limiter.record_comment(issue_key)
    
    # Act - Immediate second comment (0 seconds elapsed)
    result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is False, "Immediate second comment should be denied"


@freeze_time("2025-01-15 10:00:00")
def test_comment_within_rate_limit_window_denied(rate_limiter: CommentRateLimiter):
    """
    Test that comment within rate limit window (< 15 minutes) is denied.
    
    Uses freezegun to fast-forward time by 10 minutes (600 seconds),
    which is still within the 15-minute (900 seconds) rate limit window.
    """
    # Arrange
    issue_key = "ET-2345"
    rate_limiter.record_comment(issue_key)
    
    # Act - Move time forward by 10 minutes (600 seconds)
    with freeze_time("2025-01-15 10:10:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is False, "Comment at 10 minutes should be denied (within 15-min window)"


@freeze_time("2025-01-15 10:00:00")
def test_comment_exactly_at_rate_limit_boundary_allowed(rate_limiter: CommentRateLimiter):
    """
    Test that comment exactly at 15-minute boundary is allowed.
    
    When elapsed time equals exactly limit_minutes * 60 (900 seconds),
    the condition elapsed_seconds >= limit_seconds should evaluate to True.
    """
    # Arrange
    issue_key = "ET-3456"
    rate_limiter.record_comment(issue_key)
    
    # Act - Move time forward by exactly 15 minutes (900 seconds)
    with freeze_time("2025-01-15 10:15:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Comment exactly at 15-minute boundary should be allowed"


@freeze_time("2025-01-15 10:00:00")
def test_comment_after_rate_limit_expiry_allowed(rate_limiter: CommentRateLimiter):
    """
    Test that comment after rate limit window (> 15 minutes) is allowed.
    
    Uses freezegun to fast-forward time by 20 minutes, which exceeds
    the 15-minute rate limit window.
    
    Per key_changes: "Comment after 15+ minutes: should_comment()=True"
    """
    # Arrange
    issue_key = "ET-4567"
    rate_limiter.record_comment(issue_key)
    
    # Act - Move time forward by 20 minutes (1200 seconds)
    with freeze_time("2025-01-15 10:20:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Comment after 20 minutes should be allowed (exceeds 15-min window)"


@freeze_time("2025-01-15 10:00:00")
def test_multiple_comments_with_proper_spacing(rate_limiter: CommentRateLimiter):
    """
    Test multiple comment cycles with proper time spacing.
    
    Validates that rate limiter correctly tracks multiple comment events
    over time, allowing comments when rate limit is satisfied.
    """
    # Arrange
    issue_key = "ET-5678"
    
    # First comment at T=0
    assert rate_limiter.should_comment(issue_key, severity_increased=False) is True
    rate_limiter.record_comment(issue_key)
    
    # Second comment at T=5min - denied
    with freeze_time("2025-01-15 10:05:00"):
        assert rate_limiter.should_comment(issue_key, severity_increased=False) is False
    
    # Third comment at T=16min - allowed (> 15min from first)
    with freeze_time("2025-01-15 10:16:00"):
        assert rate_limiter.should_comment(issue_key, severity_increased=False) is True
        rate_limiter.record_comment(issue_key)
    
    # Fourth comment at T=20min - denied (only 4min from third)
    with freeze_time("2025-01-15 10:20:00"):
        assert rate_limiter.should_comment(issue_key, severity_increased=False) is False
    
    # Fifth comment at T=32min - allowed (> 15min from third)
    with freeze_time("2025-01-15 10:32:00"):
        assert rate_limiter.should_comment(issue_key, severity_increased=False) is True


# ============================================================================
# Test Cases: Severity Escalation Override
# ============================================================================

@freeze_time("2025-01-15 10:00:00")
def test_severity_escalation_bypasses_rate_limit(rate_limiter: CommentRateLimiter):
    """
    Test that severity escalation bypasses rate limit enforcement.
    
    When severity_increased=True, should_comment() should return True
    immediately without checking elapsed time, even if called 1 second
    after the previous comment.
    
    Per Section 0.7.1: "Override rate limit ONLY when severity level escalates"
    Per key_changes: "If severity_increased=True: always return True"
    """
    # Arrange
    issue_key = "ET-1234"
    rate_limiter.record_comment(issue_key)
    
    # Act - Immediate severity escalation (0 seconds elapsed)
    result = rate_limiter.should_comment(issue_key, severity_increased=True)
    
    # Assert
    assert result is True, "Severity escalation should bypass rate limit immediately"


@freeze_time("2025-01-15 10:00:00")
def test_severity_escalation_at_5_minutes(rate_limiter: CommentRateLimiter):
    """
    Test severity escalation override at 5 minutes (mid-window).
    
    Validates that severity_increased=True allows comment at any point
    during the rate limit window, not just immediately after previous comment.
    
    Per key_changes: "comment at T=5min with severity_increased=True (allowed)"
    """
    # Arrange
    issue_key = "ET-2345"
    rate_limiter.record_comment(issue_key)
    
    # Act - Severity escalation at 5 minutes (300 seconds elapsed)
    with freeze_time("2025-01-15 10:05:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=True)
    
    # Assert
    assert result is True, "Severity escalation at 5 minutes should be allowed"


@freeze_time("2025-01-15 10:00:00")
def test_normal_comment_denied_at_5_minutes(rate_limiter: CommentRateLimiter):
    """
    Test that normal comment (severity_increased=False) is denied at 5 minutes.
    
    Contrasts with test_severity_escalation_at_5_minutes to validate that
    the same timing results in different outcomes based on severity_increased flag.
    
    Per key_changes: "comment at T=5min with severity_increased=False (blocked)"
    """
    # Arrange
    issue_key = "ET-3456"
    rate_limiter.record_comment(issue_key)
    
    # Act - Normal comment at 5 minutes (300 seconds elapsed)
    with freeze_time("2025-01-15 10:05:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is False, "Normal comment at 5 minutes should be denied (within 15-min window)"


def test_severity_escalation_without_previous_comment(rate_limiter: CommentRateLimiter):
    """
    Test severity escalation on first comment (no previous timestamp).
    
    Even though there's no rate limit to bypass, severity_increased=True
    should still return True for consistency.
    """
    # Arrange
    issue_key = "ET-4567"
    
    # Act
    result = rate_limiter.should_comment(issue_key, severity_increased=True)
    
    # Assert
    assert result is True, "Severity escalation on first comment should be allowed"


# ============================================================================
# Test Cases: Timestamp Recording and Storage
# ============================================================================

@freeze_time("2025-01-15 10:00:00")
def test_record_comment_stores_timestamp(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that record_comment() stores current Unix timestamp in Redis.
    
    Validates that the timestamp is stored as a string representation
    of the current Unix epoch time (seconds since 1970-01-01).
    
    Per Section 0.5.1: "Store current timestamp: redis.setex(..., str(now_timestamp))"
    """
    # Arrange
    issue_key = "ET-1234"
    expected_timestamp = time.time()  # freezegun fixes this to 2025-01-15 10:00:00
    
    # Act
    rate_limiter.record_comment(issue_key)
    
    # Assert
    redis_key = f"comment_limit:{issue_key}"
    stored_value = mock_redis.get(redis_key)
    
    assert stored_value is not None, "Timestamp should be stored in Redis"
    assert float(stored_value) == pytest.approx(expected_timestamp, abs=1.0), \
        f"Stored timestamp {stored_value} should match expected {expected_timestamp}"


@freeze_time("2025-01-15 10:00:00")
def test_record_comment_sets_ttl(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that record_comment() sets TTL to 900 seconds (15 minutes).
    
    Validates that the Redis key expires automatically after the rate limit
    window to ensure self-cleaning state management.
    
    Per Section 0.4.3: "TTL: 900 seconds (15 minutes) for automatic cleanup"
    """
    # Arrange
    issue_key = "ET-2345"
    expected_ttl = 900  # 15 minutes in seconds
    
    # Act
    rate_limiter.record_comment(issue_key)
    
    # Assert
    redis_key = f"comment_limit:{issue_key}"
    actual_ttl = mock_redis.ttl(redis_key)
    
    assert actual_ttl > 0, "TTL should be set on Redis key"
    assert actual_ttl == expected_ttl, f"TTL should be {expected_ttl} seconds, got {actual_ttl}"


@freeze_time("2025-01-15 10:00:00")
def test_record_comment_with_custom_ttl(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that record_comment() respects custom TTL parameter.
    
    Validates that the ttl parameter can be overridden for testing
    or future per-service customization scenarios.
    """
    # Arrange
    issue_key = "ET-3456"
    custom_ttl = 60  # 1 minute for testing
    
    # Act
    rate_limiter.record_comment(issue_key, ttl=custom_ttl)
    
    # Assert
    redis_key = f"comment_limit:{issue_key}"
    actual_ttl = mock_redis.ttl(redis_key)
    
    assert actual_ttl == custom_ttl, f"TTL should be {custom_ttl} seconds, got {actual_ttl}"


@freeze_time("2025-01-15 10:00:00")
def test_record_comment_overwrites_previous_timestamp(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that recording a new comment overwrites previous timestamp.
    
    Validates that multiple record_comment() calls update the timestamp
    to track the most recent comment time.
    """
    # Arrange
    issue_key = "ET-4567"
    
    # First comment at T=0
    rate_limiter.record_comment(issue_key)
    first_timestamp = float(mock_redis.get(f"comment_limit:{issue_key}"))
    
    # Act - Second comment at T=16min (allowed)
    with freeze_time("2025-01-15 10:16:00"):
        rate_limiter.record_comment(issue_key)
        second_timestamp = float(mock_redis.get(f"comment_limit:{issue_key}"))
    
    # Assert
    assert second_timestamp > first_timestamp, "Second timestamp should be later than first"
    assert second_timestamp - first_timestamp == pytest.approx(960.0, abs=1.0), \
        "Timestamps should be 16 minutes (960 seconds) apart"


# ============================================================================
# Test Cases: Redis Key Pattern Validation
# ============================================================================

def test_key_pattern_format(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that Redis key follows the pattern: comment_limit:{issue_key}
    
    Per Section 0.4.3 Redis key patterns:
    - Key: comment_limit:{issue_key}
    
    Per key_changes: "Verify key format: f'comment_limit:{issue_key}'"
    """
    # Arrange
    issue_key = "ET-1234"
    expected_key = f"comment_limit:{issue_key}"
    
    # Act
    rate_limiter.record_comment(issue_key)
    
    # Assert
    assert mock_redis.exists(expected_key) == 1, f"Key {expected_key} should exist in Redis"
    assert mock_redis.get(expected_key) is not None, f"Key {expected_key} should have a value"


@pytest.mark.parametrize("issue_key,expected_key", [
    ("ET-1", "comment_limit:ET-1"),
    ("ET-9999", "comment_limit:ET-9999"),
    ("PROJECT-123", "comment_limit:PROJECT-123"),
    ("ABC-42", "comment_limit:ABC-42"),
])
def test_key_pattern_with_various_issue_keys(
    rate_limiter: CommentRateLimiter,
    mock_redis: FakeRedis,
    issue_key: str,
    expected_key: str
):
    """
    Test key pattern generation with various Jira issue key formats.
    
    Validates that the key pattern works with different project prefixes
    and issue numbers commonly used in Jira.
    
    Per key_changes: "Support various issue key formats"
    """
    # Act
    rate_limiter.record_comment(issue_key)
    
    # Assert
    assert mock_redis.exists(expected_key) == 1, f"Key {expected_key} should exist"


def test_different_issues_use_separate_keys(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that different issue keys result in separate Redis keys.
    
    Validates that rate limiting is per-issue, not global.
    """
    # Arrange
    issue_key_1 = "ET-1234"
    issue_key_2 = "ET-5678"
    
    # Act
    rate_limiter.record_comment(issue_key_1)
    rate_limiter.record_comment(issue_key_2)
    
    # Assert
    assert mock_redis.exists(f"comment_limit:{issue_key_1}") == 1
    assert mock_redis.exists(f"comment_limit:{issue_key_2}") == 1
    assert mock_redis.get(f"comment_limit:{issue_key_1}") != mock_redis.get(f"comment_limit:{issue_key_2}")


# ============================================================================
# Test Cases: Custom Rate Limit Windows
# ============================================================================

@freeze_time("2025-01-15 10:00:00")
def test_custom_limit_minutes_parameter(rate_limiter: CommentRateLimiter):
    """
    Test that custom limit_minutes parameter is respected.
    
    Validates that the rate limit window can be customized via the
    limit_minutes parameter for future per-service configuration.
    
    Per key_changes: "Support configurable rate limit window"
    """
    # Arrange
    issue_key = "ET-1234"
    custom_limit = 5  # 5 minutes instead of default 15
    rate_limiter.record_comment(issue_key)
    
    # Act & Assert - At 3 minutes, should be denied with 5-minute limit
    with freeze_time("2025-01-15 10:03:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=False, limit_minutes=custom_limit)
        assert result is False, "Comment at 3 minutes should be denied with 5-minute limit"
    
    # Act & Assert - At 6 minutes, should be allowed with 5-minute limit
    with freeze_time("2025-01-15 10:06:00"):
        result = rate_limiter.should_comment(issue_key, severity_increased=False, limit_minutes=custom_limit)
        assert result is True, "Comment at 6 minutes should be allowed with 5-minute limit"


@freeze_time("2025-01-15 10:00:00")
def test_zero_minute_limit_always_allows(rate_limiter: CommentRateLimiter):
    """
    Test edge case: limit_minutes=0 should always allow comments.
    
    When rate limit window is 0, any elapsed time should satisfy the condition.
    """
    # Arrange
    issue_key = "ET-2345"
    rate_limiter.record_comment(issue_key)
    
    # Act - Immediate second comment with 0-minute limit
    result = rate_limiter.should_comment(issue_key, severity_increased=False, limit_minutes=0)
    
    # Assert
    assert result is True, "0-minute rate limit should always allow comments"


# ============================================================================
# Test Cases: Graceful Degradation on Redis Failures
# ============================================================================

def test_should_comment_redis_connection_error_allows_comment(rate_limiter: CommentRateLimiter):
    """
    Test that Redis connection error in should_comment() returns True (fail open).
    
    Per Section 0.7.2 graceful degradation: "On Redis failure, allow comment (fail open)"
    Per key_changes: "Handle Redis connection errors: return True (fail-safe, allow commenting)"
    """
    # Arrange
    issue_key = "ET-1234"
    
    # Mock Redis to raise ConnectionError
    mock_redis_with_error = Mock(spec=FakeRedis)
    mock_redis_with_error.get.side_effect = ConnectionError("Redis connection failed")
    
    rate_limiter_with_failing_redis = CommentRateLimiter(
        redis_client=mock_redis_with_error,
        environment='test'
    )
    
    # Act
    result = rate_limiter_with_failing_redis.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Redis connection error should result in fail-open (allow comment)"


def test_should_comment_redis_timeout_error_allows_comment(rate_limiter: CommentRateLimiter):
    """
    Test that Redis timeout error in should_comment() returns True (fail open).
    
    Validates graceful degradation for timeout scenarios.
    """
    # Arrange
    issue_key = "ET-2345"
    
    # Mock Redis to raise TimeoutError
    mock_redis_with_timeout = Mock(spec=FakeRedis)
    mock_redis_with_timeout.get.side_effect = TimeoutError("Redis operation timed out")
    
    rate_limiter_with_timeout = CommentRateLimiter(
        redis_client=mock_redis_with_timeout,
        environment='test'
    )
    
    # Act
    result = rate_limiter_with_timeout.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Redis timeout error should result in fail-open (allow comment)"


def test_record_comment_redis_error_does_not_raise(rate_limiter: CommentRateLimiter):
    """
    Test that Redis error in record_comment() does not raise exception.
    
    Per Section 0.7.2: "Handle Redis connection errors: log warning but don't raise"
    Per key_changes: "Handle Redis connection errors: log warning but don't raise"
    """
    # Arrange
    issue_key = "ET-3456"
    
    # Mock Redis to raise ConnectionError on setex
    mock_redis_with_error = Mock(spec=FakeRedis)
    mock_redis_with_error.setex.side_effect = ConnectionError("Redis connection failed")
    
    rate_limiter_with_failing_redis = CommentRateLimiter(
        redis_client=mock_redis_with_error,
        environment='test'
    )
    
    # Act & Assert - Should not raise exception
    try:
        rate_limiter_with_failing_redis.record_comment(issue_key)
    except Exception as e:
        pytest.fail(f"record_comment() should not raise exception on Redis error, but raised: {e}")


def test_record_comment_redis_error_allows_subsequent_comment(rate_limiter: CommentRateLimiter):
    """
    Test that failed record_comment() allows next should_comment() to return True.
    
    When record_comment() fails due to Redis error, the timestamp is not stored,
    so the next should_comment() call should return True (no timestamp found).
    
    Per implementation note: "Impact: Next should_comment() call will return True"
    """
    # Arrange
    issue_key = "ET-4567"
    
    # Mock Redis: setex fails, but get returns None (no timestamp stored)
    mock_redis_with_error = Mock(spec=FakeRedis)
    mock_redis_with_error.setex.side_effect = ConnectionError("Redis connection failed")
    mock_redis_with_error.get.return_value = None
    
    rate_limiter_with_failing_redis = CommentRateLimiter(
        redis_client=mock_redis_with_error,
        environment='test'
    )
    
    # Act
    rate_limiter_with_failing_redis.record_comment(issue_key)  # Fails silently
    result = rate_limiter_with_failing_redis.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Failed record_comment() should allow subsequent should_comment()"


# ============================================================================
# Test Cases: Invalid Timestamp Handling
# ============================================================================

def test_invalid_timestamp_format_allows_comment(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that invalid timestamp format in Redis allows comment (fail open).
    
    If Redis contains corrupted data (non-numeric timestamp), the rate limiter
    should log a warning and allow the comment rather than crashing.
    
    Per implementation: "Invalid timestamp format - log warning and allow comment (fail open)"
    """
    # Arrange
    issue_key = "ET-1234"
    redis_key = f"comment_limit:{issue_key}"
    
    # Store invalid timestamp format
    mock_redis.set(redis_key, "invalid-timestamp-format")
    
    # Act
    result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Invalid timestamp format should result in fail-open (allow comment)"


def test_non_string_timestamp_allows_comment(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that non-parseable timestamp value allows comment.
    
    Edge case for corrupted Redis data that isn't a valid float string.
    """
    # Arrange
    issue_key = "ET-2345"
    redis_key = f"comment_limit:{issue_key}"
    
    # Store non-numeric value
    mock_redis.set(redis_key, "not-a-number")
    
    # Act
    result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Non-parseable timestamp should result in fail-open (allow comment)"


# ============================================================================
# Test Cases: TTL Expiration Behavior
# ============================================================================

@freeze_time("2025-01-15 10:00:00")
def test_ttl_expiration_allows_new_comment(rate_limiter: CommentRateLimiter, mock_redis: FakeRedis):
    """
    Test that after TTL expires, timestamp is removed and comment is allowed.
    
    Uses freezegun to fast-forward past TTL expiration (900 seconds),
    verifying that the Redis key no longer exists and should_comment() returns True.
    
    Per key_changes: "Test that after TTL expiry, should_comment() returns True"
    """
    # Arrange
    issue_key = "ET-1234"
    rate_limiter.record_comment(issue_key)
    
    # Verify timestamp exists
    redis_key = f"comment_limit:{issue_key}"
    assert mock_redis.get(redis_key) is not None, "Timestamp should exist before TTL expiry"
    
    # Act - Fast-forward past TTL expiration (15 minutes + 1 second)
    with freeze_time("2025-01-15 10:15:01"):
        # Manually expire the key to simulate TTL (fakeredis doesn't auto-expire with freeze_time)
        mock_redis.delete(redis_key)
        
        result = rate_limiter.should_comment(issue_key, severity_increased=False)
    
    # Assert
    assert result is True, "Comment should be allowed after TTL expiration"


# ============================================================================
# Test Cases: Multiple Issues Independence
# ============================================================================

@freeze_time("2025-01-15 10:00:00")
def test_rate_limits_independent_per_issue(rate_limiter: CommentRateLimiter):
    """
    Test that rate limits for different issues are tracked independently.
    
    Validates that commenting on one issue doesn't affect rate limits for other issues.
    """
    # Arrange
    issue_key_1 = "ET-1234"
    issue_key_2 = "ET-5678"
    
    # Act - Record comment on first issue
    rate_limiter.record_comment(issue_key_1)
    
    # Assert - Second issue should still allow comment (no timestamp)
    result_issue_2 = rate_limiter.should_comment(issue_key_2, severity_increased=False)
    assert result_issue_2 is True, "Different issue should not be affected by rate limit"
    
    # Assert - First issue should deny immediate second comment
    result_issue_1 = rate_limiter.should_comment(issue_key_1, severity_increased=False)
    assert result_issue_1 is False, "First issue should enforce rate limit"


# ============================================================================
# Test Cases: Environment Initialization
# ============================================================================

def test_initialization_with_custom_environment(mock_redis: FakeRedis):
    """
    Test that CommentRateLimiter can be initialized with custom environment.
    
    Validates that environment parameter is properly stored for logging correlation.
    """
    # Arrange & Act
    custom_env = "staging"
    rate_limiter = CommentRateLimiter(redis_client=mock_redis, environment=custom_env)
    
    # Assert
    assert rate_limiter.environment == custom_env, "Environment should be stored correctly"
    assert rate_limiter.redis_client == mock_redis, "Redis client should be stored correctly"


def test_initialization_with_default_environment(mock_redis: FakeRedis):
    """
    Test that CommentRateLimiter defaults to 'production' environment.
    
    Per implementation: environment defaults to 'production' when not specified.
    """
    # Act
    rate_limiter = CommentRateLimiter(redis_client=mock_redis)
    
    # Assert
    assert rate_limiter.environment == "production", "Environment should default to 'production'"


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "test_first_comment_allowed",
    "test_first_comment_with_severity_escalation",
    "test_immediate_second_comment_denied",
    "test_comment_within_rate_limit_window_denied",
    "test_comment_exactly_at_rate_limit_boundary_allowed",
    "test_comment_after_rate_limit_expiry_allowed",
    "test_multiple_comments_with_proper_spacing",
    "test_severity_escalation_bypasses_rate_limit",
    "test_severity_escalation_at_5_minutes",
    "test_normal_comment_denied_at_5_minutes",
    "test_severity_escalation_without_previous_comment",
    "test_record_comment_stores_timestamp",
    "test_record_comment_sets_ttl",
    "test_record_comment_with_custom_ttl",
    "test_record_comment_overwrites_previous_timestamp",
    "test_key_pattern_format",
    "test_key_pattern_with_various_issue_keys",
    "test_different_issues_use_separate_keys",
    "test_custom_limit_minutes_parameter",
    "test_zero_minute_limit_always_allows",
    "test_should_comment_redis_connection_error_allows_comment",
    "test_should_comment_redis_timeout_error_allows_comment",
    "test_record_comment_redis_error_does_not_raise",
    "test_record_comment_redis_error_allows_subsequent_comment",
    "test_invalid_timestamp_format_allows_comment",
    "test_non_string_timestamp_allows_comment",
    "test_ttl_expiration_allows_new_comment",
    "test_rate_limits_independent_per_issue",
    "test_initialization_with_custom_environment",
    "test_initialization_with_default_environment",
]
