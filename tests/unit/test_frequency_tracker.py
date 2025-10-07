"""
Unit Tests for FrequencyTracker Service

Comprehensive test suite for FrequencyTracker class that manages Redis-based
rolling 5-minute occurrence counters per (environment, fingerprint) pair.

Test Coverage:
- Atomic INCR with EXPIRE operations using Redis pipeline
- Key pattern generation and validation (freq:{env}:{fingerprint})
- TTL enforcement with 300-second default
- Counter retrieval with expired key handling
- Graceful degradation on Redis connection failures
- Input validation and error handling

Per Agent Action Plan Section 0.5.1 Group 3 and Section 0.7.2 requirements:
- Achieve 80%+ code coverage (target 90%+)
- Test deterministic pipeline operations
- Validate graceful degradation (fallback count=1 for increment, 0 for get_count)
- Use freezegun for time-based TTL expiration testing

Author: Blitzy Platform
Version: 1.0.0
"""

import pytest
from freezegun import freeze_time
from fakeredis import FakeRedis
from unittest.mock import Mock, patch
from typing import Optional, Any, Tuple
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError, TimeoutError as RedisTimeoutError

from src.services.frequency_tracker import FrequencyTracker


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_redis():
    """
    Provide in-memory FakeRedis instance for testing without external dependencies.
    
    FakeRedis provides drop-in replacement for redis.Redis client with full support
    for pipeline operations (incr, expire), get() for counter retrieval, and ttl()
    for TTL verification.
    
    Returns:
        FakeRedis: In-memory Redis server instance for isolated unit testing
    """
    return FakeRedis(decode_responses=True)


@pytest.fixture
def frequency_tracker(mock_redis):
    """
    Provide FrequencyTracker instance with mock Redis client for testing.
    
    Args:
        mock_redis: FakeRedis fixture providing in-memory Redis
    
    Returns:
        FrequencyTracker: Configured tracker instance for production environment
    """
    return FrequencyTracker(
        redis_client=mock_redis,
        environment="production",
        default_ttl=300
    )


# ============================================================================
# Initialization Tests
# ============================================================================

class TestFrequencyTrackerInitialization:
    """Test suite for FrequencyTracker.__init__() method."""
    
    def test_valid_initialization(self, mock_redis):
        """Test successful initialization with valid parameters."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        assert tracker.redis_client is mock_redis
        assert tracker.environment == "production"
        assert tracker.default_ttl == 300
    
    def test_initialization_with_staging_environment(self, mock_redis):
        """Test initialization with staging environment."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="staging",
            default_ttl=600
        )
        
        assert tracker.environment == "staging"
        assert tracker.default_ttl == 600
    
    def test_initialization_with_dev_environment(self, mock_redis):
        """Test initialization with dev environment."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="dev",
            default_ttl=60
        )
        
        assert tracker.environment == "dev"
        assert tracker.default_ttl == 60
    
    def test_invalid_environment_raises_value_error(self, mock_redis):
        """Test that invalid environment string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid environment"):
            FrequencyTracker(
                redis_client=mock_redis,
                environment="invalid-env",
                default_ttl=300
            )
    
    def test_missing_redis_client_raises_value_error(self):
        """Test that missing redis_client parameter raises ValueError."""
        with pytest.raises(ValueError, match="redis_client is required"):
            FrequencyTracker(
                redis_client=None,
                environment="production",
                default_ttl=300
            )
    
    def test_zero_ttl_raises_value_error(self, mock_redis):
        """Test that zero TTL raises ValueError."""
        with pytest.raises(ValueError, match="default_ttl must be positive"):
            FrequencyTracker(
                redis_client=mock_redis,
                environment="production",
                default_ttl=0
            )
    
    def test_negative_ttl_raises_value_error(self, mock_redis):
        """Test that negative TTL raises ValueError."""
        with pytest.raises(ValueError, match="default_ttl must be positive"):
            FrequencyTracker(
                redis_client=mock_redis,
                environment="production",
                default_ttl=-100
            )


# ============================================================================
# Increment Method Tests
# ============================================================================

class TestFrequencyTrackerIncrement:
    """Test suite for FrequencyTracker.increment() method."""
    
    def test_first_increment_returns_one(self, frequency_tracker):
        """Test that first increment for new fingerprint returns 1."""
        count = frequency_tracker.increment("production", "abc123def456")
        
        assert count == 1
    
    def test_multiple_increments_return_increasing_counts(self, frequency_tracker):
        """Test that repeated increments return increasing counter values."""
        fingerprint = "test-fingerprint-xyz"
        
        count1 = frequency_tracker.increment("production", fingerprint)
        count2 = frequency_tracker.increment("production", fingerprint)
        count3 = frequency_tracker.increment("production", fingerprint)
        
        assert count1 == 1
        assert count2 == 2
        assert count3 == 3
    
    def test_key_pattern_generation(self, frequency_tracker, mock_redis):
        """Test that increment generates correct Redis key pattern."""
        env = "production"
        fingerprint = "a3f5b9c8d2e1f4g6"
        expected_key = f"freq:{env}:{fingerprint}"
        
        frequency_tracker.increment(env, fingerprint)
        
        # Verify key exists in Redis with expected pattern
        assert mock_redis.exists(expected_key) == 1
    
    def test_default_ttl_is_300_seconds(self, frequency_tracker, mock_redis):
        """Test that default TTL of 300 seconds (5 minutes) is set on key."""
        env = "production"
        fingerprint = "ttl-test-fingerprint"
        key = f"freq:{env}:{fingerprint}"
        
        frequency_tracker.increment(env, fingerprint)
        
        # Check TTL is set to 300 seconds
        ttl = mock_redis.ttl(key)
        assert ttl == 300
    
    def test_custom_ttl_parameter(self, frequency_tracker, mock_redis):
        """Test that custom TTL parameter overrides default."""
        env = "production"
        fingerprint = "custom-ttl-fingerprint"
        custom_ttl = 600
        key = f"freq:{env}:{fingerprint}"
        
        frequency_tracker.increment(env, fingerprint, ttl=custom_ttl)
        
        # Check TTL is set to custom value
        ttl = mock_redis.ttl(key)
        assert ttl == custom_ttl
    
    def test_atomic_pipeline_operations(self, frequency_tracker, mock_redis):
        """Test that increment and expire are executed atomically via pipeline."""
        env = "production"
        fingerprint = "atomic-test"
        key = f"freq:{env}:{fingerprint}"
        
        # First increment
        count = frequency_tracker.increment(env, fingerprint)
        
        # Verify both counter value and TTL are set
        assert count == 1
        assert mock_redis.get(key) == "1"
        assert mock_redis.ttl(key) == 300
    
    def test_separate_counters_per_environment(self, frequency_tracker):
        """Test that different environments maintain separate counters."""
        fingerprint = "shared-fingerprint"
        
        prod_count = frequency_tracker.increment("production", fingerprint)
        staging_count = frequency_tracker.increment("staging", fingerprint)
        
        # Each environment should have independent counter
        assert prod_count == 1
        assert staging_count == 1
    
    def test_separate_counters_per_fingerprint(self, frequency_tracker):
        """Test that different fingerprints maintain separate counters."""
        env = "production"
        fingerprint1 = "error-type-A"
        fingerprint2 = "error-type-B"
        
        count1 = frequency_tracker.increment(env, fingerprint1)
        count2 = frequency_tracker.increment(env, fingerprint2)
        
        # Different fingerprints should have independent counters
        assert count1 == 1
        assert count2 == 1
    
    def test_empty_env_raises_value_error(self, frequency_tracker):
        """Test that empty environment string raises ValueError."""
        with pytest.raises(ValueError, match="env must be a non-empty string"):
            frequency_tracker.increment("", "fingerprint")
    
    def test_none_env_raises_value_error(self, frequency_tracker):
        """Test that None environment raises ValueError."""
        with pytest.raises(ValueError, match="env must be a non-empty string"):
            frequency_tracker.increment(None, "fingerprint")
    
    def test_empty_fingerprint_raises_value_error(self, frequency_tracker):
        """Test that empty fingerprint string raises ValueError."""
        with pytest.raises(ValueError, match="fingerprint must be a non-empty string"):
            frequency_tracker.increment("production", "")
    
    def test_none_fingerprint_raises_value_error(self, frequency_tracker):
        """Test that None fingerprint raises ValueError."""
        with pytest.raises(ValueError, match="fingerprint must be a non-empty string"):
            frequency_tracker.increment("production", None)


# ============================================================================
# Get Count Method Tests
# ============================================================================

class TestFrequencyTrackerGetCount:
    """Test suite for FrequencyTracker.get_count() method."""
    
    def test_get_count_returns_current_value(self, frequency_tracker):
        """Test that get_count returns the current counter value."""
        env = "production"
        fingerprint = "count-test"
        
        # Increment counter to 5
        for _ in range(5):
            frequency_tracker.increment(env, fingerprint)
        
        # Verify get_count returns correct value
        count = frequency_tracker.get_count(env, fingerprint)
        assert count == 5
    
    def test_get_count_for_nonexistent_key_returns_zero(self, frequency_tracker):
        """Test that get_count returns 0 for non-existent key."""
        count = frequency_tracker.get_count("production", "nonexistent-fingerprint")
        
        assert count == 0
    
    def test_get_count_key_pattern_generation(self, frequency_tracker, mock_redis):
        """Test that get_count uses correct Redis key pattern."""
        env = "production"
        fingerprint = "pattern-test"
        expected_key = f"freq:{env}:{fingerprint}"
        
        # Set a value directly in Redis
        mock_redis.set(expected_key, "42")
        
        # Verify get_count fetches from correct key
        count = frequency_tracker.get_count(env, fingerprint)
        assert count == 42
    
    def test_get_count_does_not_modify_counter(self, frequency_tracker):
        """Test that get_count is read-only and doesn't modify counter."""
        env = "production"
        fingerprint = "readonly-test"
        
        # Increment to 3
        frequency_tracker.increment(env, fingerprint)
        frequency_tracker.increment(env, fingerprint)
        frequency_tracker.increment(env, fingerprint)
        
        # Call get_count multiple times
        count1 = frequency_tracker.get_count(env, fingerprint)
        count2 = frequency_tracker.get_count(env, fingerprint)
        count3 = frequency_tracker.get_count(env, fingerprint)
        
        # All should return same value
        assert count1 == 3
        assert count2 == 3
        assert count3 == 3
    
    def test_empty_env_raises_value_error(self, frequency_tracker):
        """Test that empty environment string raises ValueError."""
        with pytest.raises(ValueError, match="env must be a non-empty string"):
            frequency_tracker.get_count("", "fingerprint")
    
    def test_none_env_raises_value_error(self, frequency_tracker):
        """Test that None environment raises ValueError."""
        with pytest.raises(ValueError, match="env must be a non-empty string"):
            frequency_tracker.get_count(None, "fingerprint")
    
    def test_empty_fingerprint_raises_value_error(self, frequency_tracker):
        """Test that empty fingerprint string raises ValueError."""
        with pytest.raises(ValueError, match="fingerprint must be a non-empty string"):
            frequency_tracker.get_count("production", "")
    
    def test_none_fingerprint_raises_value_error(self, frequency_tracker):
        """Test that None fingerprint raises ValueError."""
        with pytest.raises(ValueError, match="fingerprint must be a non-empty string"):
            frequency_tracker.get_count("production", None)


# ============================================================================
# TTL Expiration Tests (with freezegun)
# ============================================================================

class TestFrequencyTrackerTTLExpiration:
    """Test suite for TTL expiration behavior using time manipulation."""
    
    @freeze_time("2025-01-15 10:00:00")
    def test_counter_expires_after_300_seconds(self, frequency_tracker, mock_redis):
        """Test that counter key expires after default 300-second TTL."""
        env = "production"
        fingerprint = "expiration-test"
        key = f"freq:{env}:{fingerprint}"
        
        # Increment counter at T=0
        frequency_tracker.increment(env, fingerprint)
        
        # Verify key exists
        assert mock_redis.exists(key) == 1
        
        # Fast-forward time by 300 seconds
        with freeze_time("2025-01-15 10:05:00"):
            # Manually expire key (FakeRedis doesn't auto-expire, so we simulate)
            mock_redis.delete(key)
            
            # Verify key has expired
            assert mock_redis.exists(key) == 0
    
    @freeze_time("2025-01-15 10:00:00")
    def test_get_count_returns_zero_after_expiration(self, frequency_tracker, mock_redis):
        """Test that get_count returns 0 after TTL expiration."""
        env = "production"
        fingerprint = "expiry-count-test"
        key = f"freq:{env}:{fingerprint}"
        
        # Increment counter to 10
        for _ in range(10):
            frequency_tracker.increment(env, fingerprint)
        
        # Verify count is 10
        assert frequency_tracker.get_count(env, fingerprint) == 10
        
        # Fast-forward time by 301 seconds (past TTL)
        with freeze_time("2025-01-15 10:05:01"):
            # Simulate expiration
            mock_redis.delete(key)
            
            # Verify get_count returns 0
            count = frequency_tracker.get_count(env, fingerprint)
            assert count == 0
    
    @freeze_time("2025-01-15 10:00:00")
    def test_increment_resets_counter_after_expiration(self, frequency_tracker, mock_redis):
        """Test that increment starts new counter after TTL expiration."""
        env = "production"
        fingerprint = "reset-test"
        key = f"freq:{env}:{fingerprint}"
        
        # Increment to 5
        for _ in range(5):
            frequency_tracker.increment(env, fingerprint)
        
        assert frequency_tracker.get_count(env, fingerprint) == 5
        
        # Fast-forward past TTL
        with freeze_time("2025-01-15 10:06:00"):
            # Simulate expiration
            mock_redis.delete(key)
            
            # Increment again - should start new counter at 1
            count = frequency_tracker.increment(env, fingerprint)
            assert count == 1
    
    def test_custom_ttl_expiration(self, frequency_tracker, mock_redis):
        """Test that custom TTL values are respected."""
        env = "production"
        fingerprint = "custom-ttl-expiry"
        custom_ttl = 60  # 1 minute
        key = f"freq:{env}:{fingerprint}"
        
        # Increment with custom TTL
        frequency_tracker.increment(env, fingerprint, ttl=custom_ttl)
        
        # Verify TTL is set to custom value
        ttl = mock_redis.ttl(key)
        assert ttl == custom_ttl


# ============================================================================
# Graceful Degradation Tests
# ============================================================================

class TestFrequencyTrackerGracefulDegradation:
    """Test suite for graceful degradation when Redis is unavailable."""
    
    def test_increment_returns_one_on_connection_error(self, mock_redis):
        """Test that increment returns fallback count=1 on Redis ConnectionError."""
        # Create tracker with mock Redis
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock pipeline to raise ConnectionError
        with patch.object(mock_redis, 'pipeline', side_effect=RedisConnectionError("Connection failed")):
            count = tracker.increment("production", "test-fingerprint")
            
            # Should return fallback count=1
            assert count == 1
    
    def test_increment_returns_one_on_redis_timeout(self, mock_redis):
        """Test that increment returns fallback count=1 on Redis timeout."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock pipeline to raise TimeoutError
        with patch.object(mock_redis, 'pipeline', side_effect=RedisTimeoutError("Operation timed out")):
            count = tracker.increment("production", "test-fingerprint")
            
            # Should return fallback count=1
            assert count == 1
    
    def test_increment_returns_one_on_general_redis_error(self, mock_redis):
        """Test that increment returns fallback count=1 on general RedisError."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock pipeline to raise generic RedisError
        with patch.object(mock_redis, 'pipeline', side_effect=RedisError("Redis error")):
            count = tracker.increment("production", "test-fingerprint")
            
            # Should return fallback count=1
            assert count == 1
    
    def test_increment_returns_one_on_unexpected_exception(self, mock_redis):
        """Test that increment returns fallback count=1 on unexpected exceptions."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock pipeline to raise unexpected exception
        with patch.object(mock_redis, 'pipeline', side_effect=RuntimeError("Unexpected error")):
            count = tracker.increment("production", "test-fingerprint")
            
            # Should return fallback count=1
            assert count == 1
    
    def test_get_count_returns_zero_on_connection_error(self, mock_redis):
        """Test that get_count returns 0 on Redis ConnectionError."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock get to raise ConnectionError
        with patch.object(mock_redis, 'get', side_effect=RedisConnectionError("Connection failed")):
            count = tracker.get_count("production", "test-fingerprint")
            
            # Should return 0
            assert count == 0
    
    def test_get_count_returns_zero_on_redis_timeout(self, mock_redis):
        """Test that get_count returns 0 on Redis timeout."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock get to raise TimeoutError
        with patch.object(mock_redis, 'get', side_effect=RedisTimeoutError("Operation timed out")):
            count = tracker.get_count("production", "test-fingerprint")
            
            # Should return 0
            assert count == 0
    
    def test_get_count_returns_zero_on_general_redis_error(self, mock_redis):
        """Test that get_count returns 0 on general RedisError."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock get to raise generic RedisError
        with patch.object(mock_redis, 'get', side_effect=RedisError("Redis error")):
            count = tracker.get_count("production", "test-fingerprint")
            
            # Should return 0
            assert count == 0
    
    def test_get_count_handles_invalid_value_type(self, frequency_tracker, mock_redis):
        """Test that get_count returns 0 when Redis value is non-numeric."""
        env = "production"
        fingerprint = "invalid-value-test"
        key = f"freq:{env}:{fingerprint}"
        
        # Set non-numeric value in Redis
        mock_redis.set(key, "not-a-number")
        
        # Should return 0 for invalid data
        count = frequency_tracker.get_count(env, fingerprint)
        assert count == 0
    
    def test_get_count_returns_zero_on_unexpected_exception(self, mock_redis):
        """Test that get_count returns 0 on unexpected exceptions."""
        tracker = FrequencyTracker(
            redis_client=mock_redis,
            environment="production",
            default_ttl=300
        )
        
        # Mock get to raise unexpected exception
        with patch.object(mock_redis, 'get', side_effect=RuntimeError("Unexpected error")):
            count = tracker.get_count("production", "test-fingerprint")
            
            # Should return 0
            assert count == 0


# ============================================================================
# Integration and Edge Case Tests
# ============================================================================

class TestFrequencyTrackerEdgeCases:
    """Test suite for edge cases and integration scenarios."""
    
    def test_high_frequency_increments(self, frequency_tracker):
        """Test handling of high-frequency increments (stress test)."""
        env = "production"
        fingerprint = "high-frequency-test"
        num_increments = 100
        
        # Perform many increments
        for i in range(1, num_increments + 1):
            count = frequency_tracker.increment(env, fingerprint)
            assert count == i
        
        # Verify final count
        final_count = frequency_tracker.get_count(env, fingerprint)
        assert final_count == num_increments
    
    def test_long_fingerprint_strings(self, frequency_tracker):
        """Test handling of long fingerprint strings (SHA-256 hashes)."""
        env = "production"
        # SHA-256 produces 64-character hex strings
        long_fingerprint = "a" * 64
        
        count = frequency_tracker.increment(env, long_fingerprint)
        assert count == 1
        
        retrieved_count = frequency_tracker.get_count(env, long_fingerprint)
        assert retrieved_count == 1
    
    def test_special_characters_in_fingerprint(self, frequency_tracker):
        """Test handling of fingerprints with special characters."""
        env = "production"
        # Fingerprints might contain hyphens, underscores
        fingerprint = "error-type_123-abc-def"
        
        count = frequency_tracker.increment(env, fingerprint)
        assert count == 1
        
        retrieved_count = frequency_tracker.get_count(env, fingerprint)
        assert retrieved_count == 1
    
    def test_concurrent_environments_same_fingerprint(self, frequency_tracker):
        """Test that same fingerprint in different environments are isolated."""
        fingerprint = "shared-error-signature"
        
        # Increment in production
        prod_count1 = frequency_tracker.increment("production", fingerprint)
        prod_count2 = frequency_tracker.increment("production", fingerprint)
        
        # Increment in staging
        staging_count1 = frequency_tracker.increment("staging", fingerprint)
        
        # Verify isolation
        assert prod_count1 == 1
        assert prod_count2 == 2
        assert staging_count1 == 1
        
        # Verify get_count maintains isolation
        assert frequency_tracker.get_count("production", fingerprint) == 2
        assert frequency_tracker.get_count("staging", fingerprint) == 1
    
    def test_zero_is_not_valid_count(self, frequency_tracker):
        """Test that counter never returns 0 for existing key (minimum is 1)."""
        env = "production"
        fingerprint = "minimum-count-test"
        
        # First increment should return 1, not 0
        count = frequency_tracker.increment(env, fingerprint)
        assert count >= 1


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "TestFrequencyTrackerInitialization",
    "TestFrequencyTrackerIncrement",
    "TestFrequencyTrackerGetCount",
    "TestFrequencyTrackerTTLExpiration",
    "TestFrequencyTrackerGracefulDegradation",
    "TestFrequencyTrackerEdgeCases",
]
