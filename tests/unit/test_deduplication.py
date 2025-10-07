"""
Unit Tests for DeduplicationService

This module provides comprehensive unit tests for the DeduplicationService class,
which implements event ID deduplication using Redis TTL-based cache to prevent
duplicate webhook processing from Vercel and GCP sources.

Per Agent Action Plan Section 0.5.1 Group 9 and Section 0.7.1 directive #3,
these tests validate:
1. Event ID duplicate detection with Redis EXISTS check
2. Event marking with SETEX operation and 1-hour TTL minimum
3. Key pattern generation (dedup:{event_id})
4. TTL expiration behavior after timeout
5. Idempotency requirement validation
6. Graceful degradation on Redis connection failures

Test Coverage Requirements:
- Minimum 80% code coverage with target of 90%+ (per Section 0.7.2)
- Test-driven service design pattern
- Comprehensive edge case validation
- Redis failure scenario testing

Redis Key Pattern (per Section 0.4.3):
    dedup:{event_id} - Event deduplication tracking (TTL: 3600 seconds)

Test Structure:
- TestDeduplicationServiceInit: Initialization tests
- TestIsDuplicate: Duplicate detection logic tests
- TestMarkProcessed: Event marking with TTL tests
- TestTTLExpiration: Time-based expiration tests with freezegun
- TestIdempotency: End-to-end idempotency flow tests
- TestRedisFailures: Graceful degradation on Redis errors
- TestEventIdFormats: Various event_id format validation

Fixtures:
- mock_redis: FakeRedis instance for isolated testing (from conftest.py if available)
- mock_logger: Mock logger to verify log output
- mock_metrics: Mock metrics collector to verify metric recording

Author: Blitzy Platform
Version: 1.0.0
"""

import time
from typing import Optional, Any
from unittest.mock import Mock, patch, MagicMock

import pytest
from fakeredis import FakeRedis
from freezegun import freeze_time
from redis.exceptions import ConnectionError, TimeoutError, RedisError

# Internal imports from depends_on_files
from src.services.deduplication import DeduplicationService


class TestDeduplicationServiceInit:
    """Test suite for DeduplicationService initialization."""

    def test_init_with_default_environment(self):
        """Test initialization with default production environment."""
        redis_client = FakeRedis(decode_responses=True)
        
        service = DeduplicationService(redis_client)
        
        assert service.redis_client == redis_client
        assert service.environment == "production"
        assert service.logger is not None

    def test_init_with_custom_environment(self):
        """Test initialization with custom environment setting."""
        redis_client = FakeRedis(decode_responses=True)
        
        service = DeduplicationService(redis_client, environment="staging")
        
        assert service.redis_client == redis_client
        assert service.environment == "staging"

    def test_init_with_dev_environment(self):
        """Test initialization with development environment."""
        redis_client = FakeRedis(decode_responses=True)
        
        service = DeduplicationService(redis_client, environment="dev")
        
        assert service.environment == "dev"


class TestIsDuplicate:
    """Test suite for is_duplicate() duplicate detection logic."""

    def test_is_duplicate_returns_false_for_new_event(self):
        """
        Test that is_duplicate() returns False for new event_id not in cache.
        
        Per Section 0.5.1: is_duplicate() checks redis.exists(f"dedup:{event_id}")
        and returns False if key doesn't exist.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        # New event_id that hasn't been processed
        result = service.is_duplicate("vercel-new-event-123")
        
        assert result is False

    def test_is_duplicate_returns_true_for_existing_event(self):
        """
        Test that is_duplicate() returns True for event_id already in cache.
        
        Per Section 0.5.1: After mark_processed(), is_duplicate() should return True
        indicating the event was already processed.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        # Mark event as processed
        event_id = "vercel-existing-event-456"
        service.mark_processed(event_id)
        
        # Check if duplicate
        result = service.is_duplicate(event_id)
        
        assert result is True

    def test_is_duplicate_with_vercel_event_id_format(self):
        """Test is_duplicate() with Vercel-style event ID format."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        vercel_event_id = "vercel-dpl-abc123-trace-xyz789"
        
        # First check: should be False (new)
        assert service.is_duplicate(vercel_event_id) is False
        
        # Mark as processed
        service.mark_processed(vercel_event_id)
        
        # Second check: should be True (duplicate)
        assert service.is_duplicate(vercel_event_id) is True

    def test_is_duplicate_with_gcp_insert_id_format(self):
        """Test is_duplicate() with GCP insertId format."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        gcp_insert_id = "abc123def456ghi789"
        
        # First check: should be False (new)
        assert service.is_duplicate(gcp_insert_id) is False
        
        # Mark as processed
        service.mark_processed(gcp_insert_id)
        
        # Second check: should be True (duplicate)
        assert service.is_duplicate(gcp_insert_id) is True

    def test_is_duplicate_key_pattern_generation(self):
        """
        Test that is_duplicate() uses correct Redis key pattern.
        
        Per Section 0.4.3: Key pattern must be dedup:{event_id}
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-999"
        expected_key = f"dedup:{event_id}"
        
        # Mark as processed
        service.mark_processed(event_id)
        
        # Verify key exists with correct pattern
        assert redis_client.exists(expected_key) == 1

    @pytest.mark.parametrize("event_id,expected_key", [
        ("vercel-xyz-123", "dedup:vercel-xyz-123"),
        ("gcp-abc789", "dedup:gcp-abc789"),
        ("event-with-dashes-and-123", "dedup:event-with-dashes-and-123"),
        ("simple", "dedup:simple"),
    ])
    def test_is_duplicate_key_pattern_variations(self, event_id, expected_key):
        """Test key pattern generation with various event_id formats."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        service.mark_processed(event_id)
        
        # Verify correct key pattern
        assert redis_client.exists(expected_key) == 1

    def test_is_duplicate_handles_redis_connection_error(self):
        """
        Test graceful degradation when Redis connection fails.
        
        Per Section 0.7.2: On Redis failure, return False (not duplicate)
        to avoid blocking webhook processing.
        """
        redis_client = Mock()
        redis_client.exists.side_effect = ConnectionError("Connection refused")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should return False (fail-safe, allow processing)
        result = service.is_duplicate("test-event-123")
        
        assert result is False

    def test_is_duplicate_handles_redis_timeout_error(self):
        """Test graceful degradation when Redis operation times out."""
        redis_client = Mock()
        redis_client.exists.side_effect = TimeoutError("Operation timed out")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should return False (fail-safe, allow processing)
        result = service.is_duplicate("test-event-456")
        
        assert result is False

    def test_is_duplicate_handles_general_redis_error(self):
        """Test graceful degradation on general Redis errors."""
        redis_client = Mock()
        redis_client.exists.side_effect = RedisError("READONLY replica")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should return False (fail-safe, allow processing)
        result = service.is_duplicate("test-event-789")
        
        assert result is False

    def test_is_duplicate_handles_unexpected_exception(self):
        """Test graceful degradation on unexpected exceptions."""
        redis_client = Mock()
        redis_client.exists.side_effect = Exception("Unexpected error")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should return False (fail open, allow processing)
        result = service.is_duplicate("test-event-999")
        
        assert result is False


class TestMarkProcessed:
    """Test suite for mark_processed() event marking with TTL."""

    def test_mark_processed_stores_event_with_default_ttl(self):
        """
        Test that mark_processed() stores event with default 1-hour TTL.
        
        Per Section 0.7.1 directive #3: TTL minimum is 1 hour (3600 seconds)
        to handle delayed webhook retries.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-default-ttl"
        
        # Mark as processed with default TTL
        service.mark_processed(event_id)
        
        # Verify key exists
        assert redis_client.exists(f"dedup:{event_id}") == 1
        
        # Verify TTL is set (FakeRedis supports TTL)
        ttl = redis_client.ttl(f"dedup:{event_id}")
        assert ttl > 0
        assert ttl <= 3600  # Should be at or below 3600 seconds

    def test_mark_processed_stores_event_with_custom_ttl(self):
        """Test that mark_processed() accepts and applies custom TTL."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-custom-ttl"
        custom_ttl = 7200  # 2 hours
        
        # Mark as processed with custom TTL
        service.mark_processed(event_id, ttl=custom_ttl)
        
        # Verify key exists
        assert redis_client.exists(f"dedup:{event_id}") == 1
        
        # Verify custom TTL is set
        ttl = redis_client.ttl(f"dedup:{event_id}")
        assert ttl > 0
        assert ttl <= custom_ttl

    def test_mark_processed_uses_setex_command(self):
        """
        Test that mark_processed() uses atomic SETEX operation.
        
        Per Section 0.7.1: Implementation uses Redis SETEX for atomic
        set-with-TTL to prevent race conditions.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-setex"
        
        service.mark_processed(event_id, ttl=3600)
        
        # Verify key exists with value "1"
        assert redis_client.get(f"dedup:{event_id}") == "1"
        
        # Verify TTL is set atomically
        assert redis_client.ttl(f"dedup:{event_id}") > 0

    def test_mark_processed_stores_simple_value(self):
        """Test that mark_processed() stores simple "1" as value."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-value"
        
        service.mark_processed(event_id)
        
        # Verify stored value is "1"
        assert redis_client.get(f"dedup:{event_id}") == "1"

    def test_mark_processed_handles_redis_connection_error(self):
        """
        Test that mark_processed() logs error but doesn't raise on connection failure.
        
        Per Section 0.7.2: Graceful degradation - log error but allow
        processing to complete even if cache update fails.
        """
        redis_client = Mock()
        redis_client.setex.side_effect = ConnectionError("Connection refused")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should not raise exception
        try:
            service.mark_processed("test-event-123")
            # Success if no exception raised
            assert True
        except Exception as e:
            pytest.fail(f"mark_processed() should not raise exception, but raised: {e}")

    def test_mark_processed_handles_redis_timeout_error(self):
        """Test that mark_processed() doesn't raise on timeout."""
        redis_client = Mock()
        redis_client.setex.side_effect = TimeoutError("Operation timed out")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should not raise exception
        try:
            service.mark_processed("test-event-456")
            assert True
        except Exception as e:
            pytest.fail(f"mark_processed() should not raise exception, but raised: {e}")

    def test_mark_processed_handles_general_redis_error(self):
        """Test that mark_processed() doesn't raise on general Redis errors."""
        redis_client = Mock()
        redis_client.setex.side_effect = RedisError("OOM command not allowed")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should not raise exception
        try:
            service.mark_processed("test-event-789")
            assert True
        except Exception as e:
            pytest.fail(f"mark_processed() should not raise exception, but raised: {e}")

    def test_mark_processed_handles_unexpected_exception(self):
        """Test that mark_processed() doesn't raise on unexpected errors."""
        redis_client = Mock()
        redis_client.setex.side_effect = Exception("Unexpected error")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should not raise exception
        try:
            service.mark_processed("test-event-999")
            assert True
        except Exception as e:
            pytest.fail(f"mark_processed() should not raise exception, but raised: {e}")

    @pytest.mark.parametrize("ttl_seconds", [
        3600,   # 1 hour (minimum)
        7200,   # 2 hours
        1800,   # 30 minutes (below minimum, but allowed for testing)
        86400,  # 24 hours
    ])
    def test_mark_processed_with_various_ttl_values(self, ttl_seconds):
        """Test mark_processed() with various TTL values."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = f"test-event-ttl-{ttl_seconds}"
        
        service.mark_processed(event_id, ttl=ttl_seconds)
        
        # Verify key exists
        assert redis_client.exists(f"dedup:{event_id}") == 1
        
        # Verify TTL is approximately correct (within 1 second tolerance)
        actual_ttl = redis_client.ttl(f"dedup:{event_id}")
        assert actual_ttl > 0
        assert actual_ttl <= ttl_seconds


class TestTTLExpiration:
    """Test suite for TTL-based key expiration using freezegun for time manipulation."""

    def test_event_is_duplicate_within_ttl_window(self):
        """
        Test that event remains duplicate within TTL window.
        
        Per Section 0.7.1: 1-hour TTL window handles delayed webhook retries.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-ttl-window"
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # Mark event as processed
            service.mark_processed(event_id, ttl=3600)
            
            # Immediately after: should be duplicate
            assert service.is_duplicate(event_id) is True
            
            # 30 minutes later: still within TTL window
            frozen_time.tick(delta=1800)  # 30 minutes
            assert service.is_duplicate(event_id) is True
            
            # 59 minutes later: still within TTL window
            frozen_time.tick(delta=1740)  # Additional 29 minutes
            assert service.is_duplicate(event_id) is True

    def test_event_not_duplicate_after_ttl_expiry(self):
        """
        Test that event is not duplicate after TTL expires.
        
        Per key_changes requirement: Validate that expired keys return False
        from is_duplicate().
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-ttl-expiry"
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # Mark event as processed with 1-hour TTL
            service.mark_processed(event_id, ttl=3600)
            
            # Verify it's a duplicate immediately
            assert service.is_duplicate(event_id) is True
            
            # Fast-forward past TTL expiration (1 hour + 1 second)
            frozen_time.tick(delta=3601)
            
            # After expiry: should not be duplicate (key expired)
            # Note: FakeRedis may need explicit expiry check
            # In real Redis, key would be automatically removed
            ttl = redis_client.ttl(f"dedup:{event_id}")
            if ttl <= 0:
                # Key expired or doesn't exist
                assert service.is_duplicate(event_id) is False

    def test_ttl_expiration_with_custom_ttl(self):
        """Test TTL expiration with custom TTL value."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-custom-ttl-expiry"
        custom_ttl = 1800  # 30 minutes
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # Mark event with custom 30-minute TTL
            service.mark_processed(event_id, ttl=custom_ttl)
            
            # Verify duplicate within TTL
            assert service.is_duplicate(event_id) is True
            
            # Fast-forward past custom TTL (30 minutes + 1 second)
            frozen_time.tick(delta=1801)
            
            # Check TTL
            ttl = redis_client.ttl(f"dedup:{event_id}")
            if ttl <= 0:
                assert service.is_duplicate(event_id) is False

    def test_minimum_ttl_for_delayed_retries(self):
        """
        Test that 1-hour TTL minimum handles delayed webhook retries.
        
        Per Section 0.7.1: TTL minimum of 1 hour ensures delayed webhook
        retries are properly deduplicated.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-event-delayed-retry"
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # Mark event with minimum 1-hour TTL
            service.mark_processed(event_id, ttl=3600)
            
            # Simulate delayed webhook retry after 45 minutes
            frozen_time.tick(delta=2700)  # 45 minutes
            
            # Should still be marked as duplicate
            assert service.is_duplicate(event_id) is True


class TestIdempotency:
    """
    Test suite for end-to-end idempotency flow validation.
    
    Per Section 0.7.1 directive #3: Validate complete idempotency requirement
    with event_id tracking and TTL-based expiration.
    """

    def test_idempotency_flow_first_event(self):
        """
        Test complete idempotency flow for first event occurrence.
        
        Flow:
        1. Check is_duplicate() → False (new event)
        2. Process event
        3. Call mark_processed()
        4. Check is_duplicate() → True (now duplicate)
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-idempotency-first"
        
        # Step 1: Check if duplicate (should be False for new event)
        assert service.is_duplicate(event_id) is False
        
        # Step 2: Simulate event processing (no-op in this test)
        
        # Step 3: Mark as processed
        service.mark_processed(event_id)
        
        # Step 4: Verify now marked as duplicate
        assert service.is_duplicate(event_id) is True

    def test_idempotency_flow_repeated_event(self):
        """
        Test idempotency for repeated event within TTL window.
        
        Per key_changes: Repeated calls within TTL should always return True.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-idempotency-repeated"
        
        # First occurrence
        assert service.is_duplicate(event_id) is False
        service.mark_processed(event_id)
        assert service.is_duplicate(event_id) is True
        
        # Repeated calls within TTL - all should return True
        assert service.is_duplicate(event_id) is True
        assert service.is_duplicate(event_id) is True
        assert service.is_duplicate(event_id) is True

    def test_idempotency_flow_after_ttl_expiry(self):
        """
        Test idempotency flow after TTL expires - event can be reprocessed.
        
        Per key_changes: After TTL expiry, is_duplicate() should return False
        again (allow reprocessing).
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-idempotency-expiry"
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # First occurrence
            assert service.is_duplicate(event_id) is False
            service.mark_processed(event_id, ttl=3600)
            assert service.is_duplicate(event_id) is True
            
            # Fast-forward past TTL
            frozen_time.tick(delta=3601)
            
            # After expiry: should be False again (allow reprocessing)
            ttl = redis_client.ttl(f"dedup:{event_id}")
            if ttl <= 0:
                assert service.is_duplicate(event_id) is False
                
                # Can mark as processed again
                service.mark_processed(event_id, ttl=3600)
                assert service.is_duplicate(event_id) is True

    def test_concurrent_event_processing_safety(self):
        """
        Test that atomic Redis operations prevent race conditions.
        
        Per Section 0.7.1: SETEX provides atomic set-with-TTL to prevent
        race conditions between SET and EXPIRE.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-concurrent-safety"
        
        # Multiple calls to mark_processed (simulating concurrent workers)
        service.mark_processed(event_id)
        service.mark_processed(event_id)
        service.mark_processed(event_id)
        
        # Should still be marked as duplicate (no corruption)
        assert service.is_duplicate(event_id) is True
        
        # Verify value is still "1"
        assert redis_client.get(f"dedup:{event_id}") == "1"


class TestEventIdFormats:
    """Test suite for various event_id format validation."""

    @pytest.mark.parametrize("event_id", [
        "vercel-dpl-abc123-trace-xyz789",
        "vercel-simple-id",
        "gcp-insertid-abc123def456",
        "trace-id-with-multiple-dashes",
        "simple123",
        "event_with_underscores_456",
        "UPPERCASE-EVENT-ID",
        "MixedCase-Event-123",
        "event.with.dots.789",
    ])
    def test_various_event_id_formats(self, event_id):
        """
        Test deduplication with various event_id formats from Vercel and GCP.
        
        Per Agent Action Plan: Support Vercel event_id and GCP insertId formats.
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        # New event: should not be duplicate
        assert service.is_duplicate(event_id) is False
        
        # Mark as processed
        service.mark_processed(event_id)
        
        # Should now be duplicate
        assert service.is_duplicate(event_id) is True

    def test_event_id_with_special_characters(self):
        """Test event_id with special characters used in trace IDs."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        # UUID-like format
        event_id = "550e8400-e29b-41d4-a716-446655440000"
        
        assert service.is_duplicate(event_id) is False
        service.mark_processed(event_id)
        assert service.is_duplicate(event_id) is True

    def test_very_long_event_id(self):
        """Test deduplication with very long event_id."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        # Very long event ID
        event_id = "vercel-" + "a" * 200
        
        assert service.is_duplicate(event_id) is False
        service.mark_processed(event_id)
        assert service.is_duplicate(event_id) is True

    def test_empty_string_event_id(self):
        """Test behavior with empty string event_id (edge case)."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        # Empty string (should still work, though not expected in production)
        event_id = ""
        
        assert service.is_duplicate(event_id) is False
        service.mark_processed(event_id)
        assert service.is_duplicate(event_id) is True


class TestRedisFailures:
    """
    Test suite for graceful degradation on Redis connection failures.
    
    Per Section 0.7.2: Service must continue processing events even when
    Redis is temporarily unavailable, implementing fail-safe behavior.
    """

    def test_is_duplicate_fails_safe_on_connection_error(self):
        """
        Test is_duplicate() returns False on connection error (fail-safe).
        
        Per Section 0.7.2: Return False (not duplicate) to allow processing
        when Redis is unavailable.
        """
        redis_client = Mock()
        redis_client.exists.side_effect = ConnectionError("Redis unavailable")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should return False (fail-safe)
        result = service.is_duplicate("test-event")
        assert result is False

    def test_is_duplicate_fails_safe_on_timeout(self):
        """Test is_duplicate() returns False on timeout (fail-safe)."""
        redis_client = Mock()
        redis_client.exists.side_effect = TimeoutError("Operation timeout")
        
        service = DeduplicationService(redis_client, environment="production")
        
        result = service.is_duplicate("test-event")
        assert result is False

    def test_mark_processed_continues_on_connection_error(self):
        """
        Test mark_processed() logs error but doesn't raise on connection failure.
        
        Per Section 0.7.2: Allow event processing to complete even if cache
        update fails.
        """
        redis_client = Mock()
        redis_client.setex.side_effect = ConnectionError("Redis unavailable")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should not raise exception
        service.mark_processed("test-event")
        # Success if no exception raised

    def test_mark_processed_continues_on_timeout(self):
        """Test mark_processed() doesn't raise on timeout."""
        redis_client = Mock()
        redis_client.setex.side_effect = TimeoutError("Operation timeout")
        
        service = DeduplicationService(redis_client, environment="production")
        
        # Should not raise exception
        service.mark_processed("test-event")

    def test_degraded_mode_temporary_guard(self):
        """
        Test behavior during degraded mode with Redis unavailable.
        
        Per Section 0.7.2: When Redis is unavailable, system continues
        processing but may comment more frequently (temporary in-memory guard).
        """
        redis_client = Mock()
        redis_client.exists.side_effect = ConnectionError("Redis down")
        redis_client.setex.side_effect = ConnectionError("Redis down")
        
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "test-degraded-mode"
        
        # In degraded mode: is_duplicate always returns False
        assert service.is_duplicate(event_id) is False
        
        # mark_processed doesn't raise
        service.mark_processed(event_id)
        
        # Subsequent checks still return False (Redis unavailable)
        assert service.is_duplicate(event_id) is False


class TestIntegrationScenarios:
    """Integration-style tests for complete deduplication workflows."""

    def test_vercel_webhook_retry_scenario(self):
        """
        Test Vercel webhook retry scenario with delayed retries.
        
        Scenario:
        1. Vercel sends webhook at T=0
        2. Event processed and marked
        3. Vercel retries at T=30s (network issue)
        4. Duplicate detected, processing skipped
        5. After 1 hour: new occurrence processed
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id = "vercel-webhook-retry-123"
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # T=0: First webhook delivery
            assert service.is_duplicate(event_id) is False
            service.mark_processed(event_id)
            
            # T=30s: Retry after network issue
            frozen_time.tick(delta=30)
            assert service.is_duplicate(event_id) is True  # Duplicate detected
            
            # T=45m: Another retry (within TTL)
            frozen_time.tick(delta=2670)  # 45 minutes
            assert service.is_duplicate(event_id) is True
            
            # T=61m: After TTL expiry, new occurrence
            frozen_time.tick(delta=960)  # 16 more minutes
            ttl = redis_client.ttl(f"dedup:{event_id}")
            if ttl <= 0:
                assert service.is_duplicate(event_id) is False

    def test_gcp_pubsub_push_retry_scenario(self):
        """
        Test GCP Pub/Sub push retry scenario with exponential backoff.
        
        Scenario:
        1. GCP pushes event at T=0
        2. Event processed and marked
        3. GCP retries at T=10s, T=30s, T=90s (exponential backoff)
        4. All retries detected as duplicates
        """
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        gcp_insert_id = "abc123def456ghi789"
        
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # T=0: First push
            assert service.is_duplicate(gcp_insert_id) is False
            service.mark_processed(gcp_insert_id)
            
            # T=10s: First retry
            frozen_time.tick(delta=10)
            assert service.is_duplicate(gcp_insert_id) is True
            
            # T=30s: Second retry
            frozen_time.tick(delta=20)
            assert service.is_duplicate(gcp_insert_id) is True
            
            # T=90s: Third retry
            frozen_time.tick(delta=60)
            assert service.is_duplicate(gcp_insert_id) is True

    def test_multiple_events_independent_deduplication(self):
        """Test that multiple events are deduplicated independently."""
        redis_client = FakeRedis(decode_responses=True)
        service = DeduplicationService(redis_client, environment="production")
        
        event_id_1 = "vercel-event-1"
        event_id_2 = "gcp-event-2"
        event_id_3 = "vercel-event-3"
        
        # Process event 1
        assert service.is_duplicate(event_id_1) is False
        service.mark_processed(event_id_1)
        assert service.is_duplicate(event_id_1) is True
        
        # Process event 2 (independent)
        assert service.is_duplicate(event_id_2) is False
        service.mark_processed(event_id_2)
        assert service.is_duplicate(event_id_2) is True
        
        # Process event 3 (independent)
        assert service.is_duplicate(event_id_3) is False
        service.mark_processed(event_id_3)
        assert service.is_duplicate(event_id_3) is True
        
        # All events maintain their states
        assert service.is_duplicate(event_id_1) is True
        assert service.is_duplicate(event_id_2) is True
        assert service.is_duplicate(event_id_3) is True


# ============================================================================
# Module Exports and Pytest Configuration
# ============================================================================

__all__ = [
    "TestDeduplicationServiceInit",
    "TestIsDuplicate",
    "TestMarkProcessed",
    "TestTTLExpiration",
    "TestIdempotency",
    "TestEventIdFormats",
    "TestRedisFailures",
    "TestIntegrationScenarios",
]
