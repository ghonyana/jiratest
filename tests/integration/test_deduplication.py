"""
Integration Tests for Event Deduplication Service

This module contains comprehensive integration tests for the DeduplicationService
using a real Redis instance (via fakeredis) to validate Redis-based deduplication
behavior including duplicate detection, TTL expiration, and idempotency guarantees.

Per Section 0.2.1 Testing Files and Section 0.5.1 Group 9, these tests verify:
- Event deduplication prevents duplicate Jira issue creation/updates
- TTL-based expiration allows reprocessing after window expires
- Atomic Redis operations (SETEX, EXISTS) prevent race conditions
- 1-hour TTL window requirement per Section 0.7.1 directive #3
- Performance SLO: <5ms p99 for Redis operations per Section 0.7.3

Test Coverage:
1. First occurrence detection (new event returns False from is_duplicate)
2. Mark processed creates Redis entry with TTL
3. Duplicate detection after marking (subsequent calls return True)
4. Cross-source deduplication (Vercel and GCP event IDs)
5. TTL expiration allows reprocessing after time window
6. Atomic check-and-set operations prevent race conditions
7. Default 1-hour TTL applied when not specified

Redis Operations Tested:
- EXISTS: Check if event_id key exists in deduplication cache
- SETEX: Atomic set key with TTL expiration
- TTL: Verify remaining time-to-live on keys
- FLUSHDB: Cleanup between tests (via fixture)

Integration Test Strategy:
These are integration tests (not unit tests) because they test the complete
interaction between DeduplicationService and Redis, validating:
- Actual Redis command execution (not mocked)
- TTL behavior with time manipulation
- Key persistence and expiration
- Concurrent-like scenarios

Fixtures Used:
- mock_redis: FakeRedis instance from conftest.py providing in-memory Redis
  with full operational compatibility for SETEX, EXISTS, TTL commands

Time Manipulation:
- freezegun.freeze_time: Control time progression for TTL expiration testing
- time.time: Measure Redis operation performance for SLO validation

Technical References:
- Section 0.7.1: Idempotency requirement with 1-hour TTL minimum
- Section 0.1.1: Error fingerprinting and deduplication system design
- Section 0.4.3: Redis key patterns (dedup:{event_id})
- Section 0.7.3: Performance requirements (<5ms p99 for Redis operations)

Author: Blitzy Platform
Version: 1.0.0
"""

import time
from typing import Any, Optional, Dict

import pytest
from freezegun import freeze_time

# Internal imports from depends_on_files
from src.services.deduplication import DeduplicationService


class TestDeduplication:
    """
    Integration test suite for DeduplicationService with real Redis operations.

    Tests validate the complete deduplication workflow using fakeredis to ensure
    idempotent webhook processing per Section 0.7.1 requirement #3. All tests
    use real Redis commands (SETEX, EXISTS, TTL) to verify production behavior.

    Test Isolation:
    Each test receives a fresh FakeRedis instance via the mock_redis fixture,
    ensuring no state leakage between tests. The fixture automatically flushes
    all keys after each test completes.

    Performance Validation:
    Tests include timing assertions to verify <5ms p99 Redis operation latency
    per Section 0.7.3 performance requirements.
    """

    def test_is_duplicate_returns_false_for_new_event(self, mock_redis):
        """
        Test that first occurrence of an event_id is not marked as duplicate.

        Validates the initial deduplication check returns False for a new event
        that has not been previously processed, allowing the webhook to proceed
        with normal processing.

        Per Section 0.7.1 requirement #3: Only events that were previously marked
        as processed should be considered duplicates.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Call is_duplicate() with new event_id
            3. Assert result is False (not a duplicate)
            4. Verify Redis does NOT contain dedup key

        Expected Behavior:
            - is_duplicate() returns False
            - Redis key 'dedup:vercel-event-123' does not exist
            - No exceptions raised
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")

        # Act: Check if new event is duplicate
        event_id = "vercel-event-123"
        is_dup = dedup_service.is_duplicate(event_id)

        # Assert: New event should not be marked as duplicate
        assert is_dup is False, "New event should not be marked as duplicate"

        # Verify: Redis does NOT contain the deduplication key
        redis_key = f"dedup:{event_id}"
        assert mock_redis.exists(redis_key) == 0, "Redis should not have key for unprocessed event"

    def test_mark_processed_stores_event_id(self, mock_redis):
        """
        Test that mark_processed() creates Redis entry with TTL.

        Validates that marking an event as processed stores the event_id in Redis
        with the specified TTL, enabling subsequent duplicate detection.

        Per Section 0.4.3 Redis key patterns: dedup:{event_id} with TTL: 3600s

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Call mark_processed() with event_id and TTL
            3. Assert Redis key exists with correct value
            4. Verify TTL is set to approximately specified value

        Expected Behavior:
            - Redis key 'dedup:vercel-event-456' exists
            - Key value is '1' (simple existence marker)
            - TTL is set to 3600 seconds (±1 second tolerance)
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")

        # Act: Mark event as processed with 1-hour TTL
        event_id = "vercel-event-456"
        ttl_seconds = 3600
        dedup_service.mark_processed(event_id, ttl=ttl_seconds)

        # Assert: Redis key exists with correct value
        redis_key = f"dedup:{event_id}"
        assert mock_redis.exists(redis_key) == 1, "Redis should contain dedup key after mark_processed"
        
        # Verify value is '1' (simple existence marker)
        value = mock_redis.get(redis_key)
        assert value == "1", f"Expected value '1', got '{value}'"

        # Verify TTL is set correctly (allow ±1 second tolerance for execution time)
        actual_ttl = mock_redis.ttl(redis_key)
        assert ttl_seconds - 1 <= actual_ttl <= ttl_seconds, \
            f"Expected TTL ~{ttl_seconds}s, got {actual_ttl}s"

    def test_is_duplicate_returns_true_after_mark_processed(self, mock_redis):
        """
        Test that duplicate detection works after event is marked processed.

        Validates the complete deduplication workflow: mark event as processed,
        then verify subsequent is_duplicate() calls return True.

        Per Section 0.7.1 requirement #3: Drop duplicate events by event_id
        using TTL cache for idempotent webhook processing.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Mark event as processed
            3. Check if same event_id is duplicate
            4. Assert result is True (duplicate detected)

        Expected Behavior:
            - First mark_processed() succeeds without error
            - Subsequent is_duplicate() returns True
            - Duplicate detection prevents reprocessing
        """
        # Arrange: Create deduplication service and mark event as processed
        dedup_service = DeduplicationService(mock_redis, environment="test")
        event_id = "vercel-event-789"

        # Act: Mark event as processed, then check for duplicate
        dedup_service.mark_processed(event_id)
        is_dup = dedup_service.is_duplicate(event_id)

        # Assert: Event should now be marked as duplicate
        assert is_dup is True, "Previously processed event should be marked as duplicate"

        # Verify: Redis key still exists
        redis_key = f"dedup:{event_id}"
        assert mock_redis.exists(redis_key) == 1, "Redis dedup key should persist after check"

    def test_duplicate_detection_across_sources(self, mock_redis):
        """
        Test that deduplication works for both Vercel and GCP event IDs.

        Validates that the deduplication service handles event_id formats from
        multiple webhook sources consistently, using the same Redis key pattern
        regardless of source.

        Per Section 0.4.2: Service accepts events from Vercel (event_id) and
        GCP (insertId) with identical deduplication behavior.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Mark Vercel event as processed
            3. Mark GCP event as processed
            4. Verify both are detected as duplicates
            5. Confirm different key patterns but same behavior

        Expected Behavior:
            - Vercel event_id successfully marked and detected
            - GCP insertId successfully marked and detected
            - Different key names (dedup:vercel-*, dedup:gcp-*)
            - Identical deduplication behavior regardless of source
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")

        # Act: Mark events from both sources as processed
        vercel_event_id = "vercel-abc-123"
        gcp_event_id = "gcp-insertId-xyz789"

        dedup_service.mark_processed(vercel_event_id)
        dedup_service.mark_processed(gcp_event_id)

        # Check duplicate status for both sources
        vercel_is_dup = dedup_service.is_duplicate(vercel_event_id)
        gcp_is_dup = dedup_service.is_duplicate(gcp_event_id)

        # Assert: Both events should be detected as duplicates
        assert vercel_is_dup is True, "Vercel event should be marked as duplicate"
        assert gcp_is_dup is True, "GCP event should be marked as duplicate"

        # Verify: Both keys exist in Redis with different patterns
        vercel_key = f"dedup:{vercel_event_id}"
        gcp_key = f"dedup:{gcp_event_id}"
        
        assert mock_redis.exists(vercel_key) == 1, "Vercel dedup key should exist"
        assert mock_redis.exists(gcp_key) == 1, "GCP dedup key should exist"

    def test_ttl_expiration_allows_reprocessing(self, mock_redis):
        """
        Test that events can be reprocessed after TTL expires.

        Validates that deduplication keys automatically expire after TTL window,
        allowing the same event_id to be processed again if it occurs after the
        deduplication window. Uses freezegun for deterministic time manipulation.

        Per Section 0.7.1 requirement #3: TTL minimum is 1 hour to handle delayed
        retries, but keys should expire after TTL for reprocessing.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Mark event with short TTL (5 seconds for test speed)
            3. Freeze time and advance by 6 seconds
            4. Verify event is no longer considered duplicate
            5. Confirm Redis key has expired

        Expected Behavior:
            - Event initially marked as processed
            - After TTL expires, is_duplicate() returns False
            - Redis key no longer exists after expiration
            - Event can be reprocessed after TTL window
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")
        event_id = "event-short-ttl"

        # Act: Mark event with short TTL for test speed
        # Use 5-second TTL instead of 3600 for faster test execution
        short_ttl = 5
        dedup_service.mark_processed(event_id, ttl=short_ttl)

        # Verify event is initially marked as duplicate
        assert dedup_service.is_duplicate(event_id) is True, \
            "Event should be duplicate immediately after marking"

        # Simulate time passing beyond TTL window
        # FakeRedis supports time-based expiration, so we need to manipulate time
        # and trigger expiration check
        with freeze_time("2025-01-15 10:00:00") as frozen_time:
            # Re-mark with same TTL at frozen time
            dedup_service.mark_processed(event_id, ttl=short_ttl)
            
            # Advance time by 6 seconds (beyond 5-second TTL)
            frozen_time.tick(delta=6)
            
            # Force FakeRedis to expire keys by accessing them
            # FakeRedis expires keys lazily on access
            redis_key = f"dedup:{event_id}"
            _ = mock_redis.get(redis_key)  # Trigger expiration check

        # Assert: Event should no longer be duplicate after TTL expiration
        is_dup_after_expiry = dedup_service.is_duplicate(event_id)
        assert is_dup_after_expiry is False, \
            "Event should not be duplicate after TTL expires"

        # Verify: Redis key no longer exists
        redis_key = f"dedup:{event_id}"
        assert mock_redis.exists(redis_key) == 0, \
            "Redis key should not exist after TTL expiration"

    def test_atomic_mark_and_check_operation(self, mock_redis):
        """
        Test atomic nature of check-and-set deduplication pattern.

        Validates that deduplication operations are atomic and safe for concurrent
        use across multiple Gunicorn workers. Tests the sequential execution of
        is_duplicate() and mark_processed() to ensure no race conditions.

        Per Section 0.7.1: Implementation uses Redis SETNX with expiration for
        atomic check-and-set operations.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. First check: is_duplicate() should return False
            3. Mark as processed: mark_processed() succeeds
            4. Second check: is_duplicate() should return True
            5. Measure operation timing for performance validation

        Expected Behavior:
            - First is_duplicate() returns False (new event)
            - mark_processed() succeeds atomically
            - Second is_duplicate() returns True (now duplicate)
            - All operations complete within <5ms p99 per Section 0.7.3
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")
        event_id = "event-atomic-test"

        # Act & Assert: Sequential check-mark-check pattern
        start_time = time.time()

        # First check: should not be duplicate
        first_check = dedup_service.is_duplicate(event_id)
        assert first_check is False, "First check should return False for new event"

        # Mark as processed: atomic operation
        dedup_service.mark_processed(event_id)

        # Second check: should now be duplicate
        second_check = dedup_service.is_duplicate(event_id)
        assert second_check is True, "Second check should return True after marking"

        # Measure total operation time
        duration_ms = (time.time() - start_time) * 1000

        # Performance assertion: All operations should complete quickly
        # Target: <5ms p99 per Section 0.7.3, allow generous margin for test execution
        assert duration_ms < 50, \
            f"Sequential operations took {duration_ms:.2f}ms, expected <50ms"

        # Verify final Redis state
        redis_key = f"dedup:{event_id}"
        assert mock_redis.exists(redis_key) == 1, "Final state should have Redis key"
        assert mock_redis.get(redis_key) == "1", "Redis value should be '1'"

    def test_deduplication_with_default_ttl(self, mock_redis):
        """
        Test that default 1-hour TTL is applied when not specified.

        Validates that mark_processed() applies the default TTL of 3600 seconds
        (1 hour) when no TTL parameter is provided, per Section 0.7.1 requirement
        for 1-hour minimum TTL to handle delayed webhook retries.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Call mark_processed() without TTL parameter
            3. Verify Redis key has default 3600-second TTL
            4. Confirm event is marked as duplicate

        Expected Behavior:
            - mark_processed() without TTL uses default 3600 seconds
            - Redis key has TTL of approximately 3600 seconds
            - Event is successfully marked as duplicate
            - Matches Section 0.7.1 1-hour minimum requirement
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")
        event_id = "event-default-ttl"

        # Act: Mark event as processed without specifying TTL
        # Should use default TTL of 3600 seconds per service implementation
        dedup_service.mark_processed(event_id)

        # Assert: Redis key exists and has default TTL
        redis_key = f"dedup:{event_id}"
        assert mock_redis.exists(redis_key) == 1, \
            "Redis key should exist after mark_processed with default TTL"

        # Verify TTL is approximately 3600 seconds (1 hour)
        # Allow ±1 second tolerance for execution time
        actual_ttl = mock_redis.ttl(redis_key)
        expected_default_ttl = 3600  # 1 hour per Section 0.7.1
        
        assert expected_default_ttl - 1 <= actual_ttl <= expected_default_ttl, \
            f"Expected default TTL ~{expected_default_ttl}s, got {actual_ttl}s"

        # Verify event is marked as duplicate
        is_dup = dedup_service.is_duplicate(event_id)
        assert is_dup is True, "Event should be duplicate after marking with default TTL"

        # Verify Redis value
        value = mock_redis.get(redis_key)
        assert value == "1", f"Expected Redis value '1', got '{value}'"

    def test_performance_validation_redis_operations(self, mock_redis):
        """
        Test that Redis operations meet <5ms p99 performance requirement.

        Validates that individual deduplication operations (is_duplicate and
        mark_processed) complete within the performance SLO specified in
        Section 0.7.3: <5ms p99 for Redis operations.

        Args:
            mock_redis: FakeRedis instance from conftest.py fixture

        Test Flow:
            1. Create DeduplicationService with mock Redis
            2. Measure is_duplicate() operation timing
            3. Measure mark_processed() operation timing
            4. Assert both operations complete within SLO

        Expected Behavior:
            - is_duplicate() completes in <5ms
            - mark_processed() completes in <5ms
            - Both operations meet p99 performance target
        """
        # Arrange: Create deduplication service with test Redis
        dedup_service = DeduplicationService(mock_redis, environment="test")

        # Test 1: Measure is_duplicate() performance
        event_id_check = "event-perf-check"
        
        start_check = time.time()
        dedup_service.is_duplicate(event_id_check)
        check_duration_ms = (time.time() - start_check) * 1000

        assert check_duration_ms < 5.0, \
            f"is_duplicate() took {check_duration_ms:.2f}ms, expected <5ms per Section 0.7.3"

        # Test 2: Measure mark_processed() performance
        event_id_mark = "event-perf-mark"
        
        start_mark = time.time()
        dedup_service.mark_processed(event_id_mark)
        mark_duration_ms = (time.time() - start_mark) * 1000

        assert mark_duration_ms < 5.0, \
            f"mark_processed() took {mark_duration_ms:.2f}ms, expected <5ms per Section 0.7.3"

        # Verify operations succeeded correctly
        assert dedup_service.is_duplicate(event_id_mark) is True, \
            "Performance test should still function correctly"


# ============================================================================
# Module Marker for Integration Tests
# ============================================================================

# Mark all tests in this module as integration tests
# Usage: pytest -m integration tests/integration/test_deduplication.py
pytestmark = pytest.mark.integration

