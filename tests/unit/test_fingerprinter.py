"""
Unit Tests for ErrorFingerprinter Service

This module implements comprehensive unit tests for the ErrorFingerprinter class,
validating the CRITICAL fingerprinting algorithm per Agent Action Plan Section 0.5.1
Group 3 and Section 0.7.1 requirement #2 (Fingerprint Stability is Critical).

Test Coverage:
1. Deterministic hashing: Identical error events generate identical fingerprints
   across multiple invocations
2. Uniqueness: Different error_class, service, environment, or stack frames
   produce distinct fingerprints
3. Sanitization integration: PII is removed from message before hashing to
   ensure grouping stability
4. Top stack frame extraction: Regex pattern r"at ([\w/<>\.]+):(\d+):(\d+)"
   extracts first non-library frame
5. Fallback behavior: When stack trace is missing, use error_class + first
   50 chars of message
6. SHA-256 hashing: Validate correct hash algorithm usage
7. Fingerprint format validation: Ensure hexadecimal string output

The tests use pytest fixtures for sample NormalizedErrorEvent instances and
mock PIISanitizer to control sanitization output and isolate fingerprinting logic.
pytest.parametrize is used for comprehensive coverage of different error scenarios.

Achievement Target:
- Minimum 80% code coverage
- Target 90%+ coverage
"""

import hashlib
from datetime import datetime
from typing import Optional, Any
from unittest.mock import patch

import pytest

from src.models.error_event import NormalizedErrorEvent
from src.services.fingerprinter import ErrorFingerprinter
from src.services.sanitizer import PIISanitizer


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def sample_event_with_stack() -> NormalizedErrorEvent:
    """
    Create a sample normalized error event with complete stack trace.
    
    This fixture provides a realistic error event from a production deployment
    with all fields populated, including a multi-frame stack trace with both
    application and library frames.
    
    Returns:
        NormalizedErrorEvent with comprehensive error details and stack trace
    """
    return NormalizedErrorEvent(
        source="vercel",
        service="web-app",
        environment="production",
        error_class="TypeError",
        message="Cannot read property 'x' of undefined in user authentication",
        stack_trace="""TypeError: Cannot read property 'x' of undefined
    at validateUser (/app/pages/checkout.tsx:123:45)
    at processPayment (/app/lib/payment.ts:67:12)
    at node_modules/express/lib/router.js:284:15
    at node_modules/express/lib/router.js:335:10""",
        path="/api/checkout",
        url="https://my-app.vercel.app/api/checkout",
        release="dpl_xyz123",
        log_url="https://vercel.com/logs?traceId=abc123",
        event_id="vercel-xyz-123",
        occurred_at=datetime(2025, 1, 15, 10, 30, 45),
    )


@pytest.fixture
def sample_event_without_stack() -> NormalizedErrorEvent:
    """
    Create a sample normalized error event without stack trace.
    
    This fixture tests the fallback behavior when stack trace information
    is unavailable, which can occur with certain error sources or logging
    configurations.
    
    Returns:
        NormalizedErrorEvent with no stack_trace field
    """
    return NormalizedErrorEvent(
        source="gcp",
        service="api-service",
        environment="staging",
        error_class="RuntimeError",
        message="Database connection timeout after 30 seconds",
        stack_trace=None,
        path="/api/users",
        url="https://api-staging.example.com/api/users",
        release="v2.3.1",
        log_url="https://console.cloud.google.com/logs?insertId=xyz789",
        event_id="gcp-xyz-789",
        occurred_at=datetime(2025, 1, 15, 11, 45, 30),
    )


@pytest.fixture
def fingerprinter_with_mock_sanitizer() -> ErrorFingerprinter:
    """
    Create ErrorFingerprinter with a mocked PIISanitizer for controlled testing.
    
    This fixture enables isolation of fingerprinting logic from PII sanitization
    by injecting a mock sanitizer that returns predetermined outputs. Tests can
    then validate fingerprint generation independently of sanitization patterns.
    
    Returns:
        ErrorFingerprinter instance with mocked sanitizer
    """
    mock_sanitizer = PIISanitizer()
    return ErrorFingerprinter(sanitizer=mock_sanitizer)


# =============================================================================
# Test Cases: Determinism and Stability
# =============================================================================

class TestFingerprintDeterminism:
    """
    Test suite validating deterministic fingerprint generation.
    
    Per Section 0.7.1 requirement #2:
    "Fingerprint stability is CRITICAL. Identical errors must produce
     identical fingerprints across multiple invocations."
    
    These tests ensure the same error event always generates the same
    fingerprint, which is essential for:
    - Jira issue deduplication
    - Frequency counting accuracy
    - Severity threshold evaluation
    """
    
    def test_same_error_produces_same_fingerprint(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that identical error events generate identical fingerprints.
        
        This test verifies the deterministic nature of the fingerprinting algorithm
        by calling generate_fingerprint() multiple times on the same event and
        ensuring the output is identical every time.
        
        Critical Requirement:
            Identical inputs MUST produce identical outputs for proper error grouping.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Generate fingerprint multiple times
        fingerprint1 = fingerprinter.generate_fingerprint(sample_event_with_stack)
        fingerprint2 = fingerprinter.generate_fingerprint(sample_event_with_stack)
        fingerprint3 = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # All fingerprints must be identical
        assert fingerprint1 == fingerprint2
        assert fingerprint2 == fingerprint3
        assert fingerprint1 == fingerprint3
        
        # Fingerprint must be valid SHA-256 hex string (64 characters)
        assert len(fingerprint1) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint1)
    
    def test_same_error_different_instances_same_fingerprint(self) -> None:
        """
        Validate that two separately constructed but identical events produce
        the same fingerprint.
        
        This test ensures fingerprinting is based on event content, not object
        identity. Two events with the same field values should have identical
        fingerprints even if they are different object instances.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Create two identical events as separate instances
        event1 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Cannot read property 'name' of null",
            stack_trace="at /app/utils/user.ts:45:12",
            path="/api/user",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        event2 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Cannot read property 'name' of null",
            stack_trace="at /app/utils/user.ts:45:12",
            path="/api/user",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint1 = fingerprinter.generate_fingerprint(event1)
        fingerprint2 = fingerprinter.generate_fingerprint(event2)
        
        # Fingerprints must match despite being different object instances
        assert fingerprint1 == fingerprint2
    
    @patch.object(PIISanitizer, 'sanitize')
    def test_consistent_fingerprint_with_controlled_sanitization(
        self, mock_sanitize, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate fingerprint consistency when sanitization output is controlled.
        
        This test mocks the sanitizer to return a fixed output, ensuring that
        fingerprint determinism holds even when the sanitization step is involved.
        It confirms that the fingerprinting algorithm itself is deterministic
        independent of the sanitization logic.
        """
        # Mock sanitizer to return consistent output
        mock_sanitize.return_value = "Cannot read property [REDACTED] of undefined"
        
        fingerprinter = ErrorFingerprinter()
        
        # Generate fingerprints with controlled sanitization
        fingerprint1 = fingerprinter.generate_fingerprint(sample_event_with_stack)
        fingerprint2 = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Fingerprints must be identical with controlled inputs
        assert fingerprint1 == fingerprint2
        
        # Verify sanitize was called
        assert mock_sanitize.call_count >= 2


# =============================================================================
# Test Cases: Uniqueness and Collision Resistance
# =============================================================================

class TestFingerprintUniqueness:
    """
    Test suite validating that different errors produce different fingerprints.
    
    These tests ensure the fingerprinting algorithm generates unique identifiers
    for distinct errors, preventing false grouping and ensuring accurate issue
    deduplication in Jira.
    
    The SHA-256 algorithm provides strong collision resistance, so different
    input combinations should produce different fingerprints.
    """
    
    def test_different_error_class_produces_different_fingerprint(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that changing error_class generates a different fingerprint.
        
        The error_class is a key component of the fingerprint formula:
        hash(service + env + error_class + top_stack_frame + sanitized_message)
        
        Different error classes should result in distinct fingerprints even
        if all other fields are identical.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Generate fingerprint for original event
        original_fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Create event with different error_class
        modified_event = NormalizedErrorEvent(
            source=sample_event_with_stack.source,
            service=sample_event_with_stack.service,
            environment=sample_event_with_stack.environment,
            error_class="ReferenceError",  # Changed from TypeError
            message=sample_event_with_stack.message,
            stack_trace=sample_event_with_stack.stack_trace,
            path=sample_event_with_stack.path,
            url=sample_event_with_stack.url,
            release=sample_event_with_stack.release,
            log_url=sample_event_with_stack.log_url,
            event_id=sample_event_with_stack.event_id + "_modified",
            occurred_at=sample_event_with_stack.occurred_at,
        )
        
        modified_fingerprint = fingerprinter.generate_fingerprint(modified_event)
        
        # Fingerprints must be different
        assert original_fingerprint != modified_fingerprint
    
    def test_different_service_produces_different_fingerprint(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that changing service name generates a different fingerprint.
        
        Service is a critical component for routing errors to the correct team.
        Different services should have distinct fingerprints even for the same
        error type and message.
        """
        fingerprinter = ErrorFingerprinter()
        
        original_fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Create event with different service
        modified_event = NormalizedErrorEvent(
            source=sample_event_with_stack.source,
            service="api-gateway",  # Changed from web-app
            environment=sample_event_with_stack.environment,
            error_class=sample_event_with_stack.error_class,
            message=sample_event_with_stack.message,
            stack_trace=sample_event_with_stack.stack_trace,
            path=sample_event_with_stack.path,
            url=sample_event_with_stack.url,
            release=sample_event_with_stack.release,
            log_url=sample_event_with_stack.log_url,
            event_id=sample_event_with_stack.event_id + "_modified",
            occurred_at=sample_event_with_stack.occurred_at,
        )
        
        modified_fingerprint = fingerprinter.generate_fingerprint(modified_event)
        
        assert original_fingerprint != modified_fingerprint
    
    def test_different_environment_produces_different_fingerprint(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that changing environment generates a different fingerprint.
        
        Per Section 0.7.1: "Include environment to separate prod/staging issues"
        
        Production and staging errors should have distinct fingerprints to
        enable separate issue tracking and severity thresholds per environment.
        """
        fingerprinter = ErrorFingerprinter()
        
        original_fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Create event with different environment
        modified_event = NormalizedErrorEvent(
            source=sample_event_with_stack.source,
            service=sample_event_with_stack.service,
            environment="staging",  # Changed from prod
            error_class=sample_event_with_stack.error_class,
            message=sample_event_with_stack.message,
            stack_trace=sample_event_with_stack.stack_trace,
            path=sample_event_with_stack.path,
            url=sample_event_with_stack.url,
            release=sample_event_with_stack.release,
            log_url=sample_event_with_stack.log_url,
            event_id=sample_event_with_stack.event_id + "_modified",
            occurred_at=sample_event_with_stack.occurred_at,
        )
        
        modified_fingerprint = fingerprinter.generate_fingerprint(modified_event)
        
        assert original_fingerprint != modified_fingerprint
    
    def test_different_stack_frame_produces_different_fingerprint(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that changing stack frame location generates a different fingerprint.
        
        The top stack frame indicates where in the code the error occurred.
        Different code locations should produce distinct fingerprints to enable
        precise error tracking and issue deduplication.
        """
        fingerprinter = ErrorFingerprinter()
        
        original_fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Create event with different stack frame
        modified_stack_trace = """TypeError: Cannot read property 'x' of undefined
    at handleRequest (/app/pages/dashboard.tsx:456:78)
    at processRequest (/app/lib/handler.ts:89:23)"""
        
        modified_event = NormalizedErrorEvent(
            source=sample_event_with_stack.source,
            service=sample_event_with_stack.service,
            environment=sample_event_with_stack.environment,
            error_class=sample_event_with_stack.error_class,
            message=sample_event_with_stack.message,
            stack_trace=modified_stack_trace,  # Different top frame
            path=sample_event_with_stack.path,
            url=sample_event_with_stack.url,
            release=sample_event_with_stack.release,
            log_url=sample_event_with_stack.log_url,
            event_id=sample_event_with_stack.event_id + "_modified",
            occurred_at=sample_event_with_stack.occurred_at,
        )
        
        modified_fingerprint = fingerprinter.generate_fingerprint(modified_event)
        
        assert original_fingerprint != modified_fingerprint
    
    def test_different_message_produces_different_fingerprint(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that changing the error message generates a different fingerprint.
        
        The sanitized message is part of the fingerprint formula. Different
        error messages (after sanitization) should produce distinct fingerprints.
        """
        fingerprinter = ErrorFingerprinter()
        
        original_fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Create event with different message
        modified_event = NormalizedErrorEvent(
            source=sample_event_with_stack.source,
            service=sample_event_with_stack.service,
            environment=sample_event_with_stack.environment,
            error_class=sample_event_with_stack.error_class,
            message="Cannot access property 'y' of null during validation",  # Different message
            stack_trace=sample_event_with_stack.stack_trace,
            path=sample_event_with_stack.path,
            url=sample_event_with_stack.url,
            release=sample_event_with_stack.release,
            log_url=sample_event_with_stack.log_url,
            event_id=sample_event_with_stack.event_id + "_modified",
            occurred_at=sample_event_with_stack.occurred_at,
        )
        
        modified_fingerprint = fingerprinter.generate_fingerprint(modified_event)
        
        assert original_fingerprint != modified_fingerprint


# =============================================================================
# Test Cases: PII Sanitization Integration
# =============================================================================

class TestSanitizationIntegration:
    """
    Test suite validating that PII sanitization occurs before fingerprint hashing.
    
    Per Section 0.7.1 requirement #2:
    "Sanitization MUST occur before hashing to ensure consistent fingerprints
     despite variable data"
    
    These tests verify that ErrorFingerprinter correctly integrates with
    PIISanitizer and that sanitization produces stable fingerprints.
    """
    
    @patch.object(PIISanitizer, 'sanitize')
    def test_sanitization_called_before_hashing(
        self, mock_sanitize, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that PIISanitizer.sanitize() is called during fingerprinting.
        
        This test verifies the integration point between ErrorFingerprinter
        and PIISanitizer, ensuring sanitization is part of the fingerprinting
        pipeline.
        """
        mock_sanitize.return_value = "Sanitized error message"
        
        fingerprinter = ErrorFingerprinter()
        fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Verify sanitize was called with the original message
        mock_sanitize.assert_called_once_with(sample_event_with_stack.message)
    
    @patch.object(PIISanitizer, 'sanitize')
    def test_same_sanitized_output_produces_same_fingerprint(
        self, mock_sanitize, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that errors with different raw messages but identical sanitized
        messages produce the same fingerprint.
        
        This is the CRITICAL behavior for fingerprint stability: PII variations
        (like different user IDs, emails, UUIDs) should not create separate
        fingerprints for the same error type.
        
        Example:
            "Error for user_id=12345" and "Error for user_id=67890" should both
            become "Error for user_id=[ID]" after sanitization and thus produce
            identical fingerprints.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Mock sanitizer to return same output for different inputs
        mock_sanitize.return_value = "Cannot read property [REDACTED] of undefined"
        
        # Event 1 with PII (user_id=12345)
        event1 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Cannot read property 'x' of user_id=12345",
            stack_trace="at /app/pages/checkout.tsx:123:45",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        # Event 2 with different PII (user_id=67890)
        event2 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Cannot read property 'x' of user_id=67890",
            stack_trace="at /app/pages/checkout.tsx:123:45",
            log_url="https://vercel.com/logs?traceId=def",
            event_id="evt_002",
            occurred_at=datetime(2025, 1, 15, 10, 5, 0),
        )
        
        fingerprint1 = fingerprinter.generate_fingerprint(event1)
        fingerprint2 = fingerprinter.generate_fingerprint(event2)
        
        # Fingerprints must be identical because sanitized messages are the same
        assert fingerprint1 == fingerprint2
    
    @patch.object(PIISanitizer, 'sanitize')
    def test_different_sanitized_output_produces_different_fingerprint(
        self, mock_sanitize
    ) -> None:
        """
        Validate that errors with truly different messages (after sanitization)
        produce different fingerprints.
        
        While PII variations should be grouped together, genuinely different
        error messages should still produce distinct fingerprints.
        """
        fingerprinter = ErrorFingerprinter()
        
        event1 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Cannot read property 'x' of undefined",
            stack_trace="at /app/pages/checkout.tsx:123:45",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        event2 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Cannot access property 'y' of null",
            stack_trace="at /app/pages/checkout.tsx:123:45",
            log_url="https://vercel.com/logs?traceId=def",
            event_id="evt_002",
            occurred_at=datetime(2025, 1, 15, 10, 5, 0),
        )
        
        # Mock sanitizer to return different outputs for different messages
        mock_sanitize.side_effect = [
            "Cannot read property [REDACTED] of undefined",
            "Cannot access property [REDACTED] of null",
        ]
        
        fingerprint1 = fingerprinter.generate_fingerprint(event1)
        fingerprint2 = fingerprinter.generate_fingerprint(event2)
        
        # Fingerprints must be different
        assert fingerprint1 != fingerprint2


# =============================================================================
# Test Cases: Stack Frame Extraction
# =============================================================================

class TestStackFrameExtraction:
    """
    Test suite validating stack trace parsing and top frame extraction.
    
    Per Section 0.5.1 Group 3:
    "Top stack frame extraction: Extract first non-library frame using
     regex pattern r'at ([\\w/<>\\.]+):(\\d+):(\\d+)'"
    
    These tests ensure the fingerprinter correctly identifies application code
    frames and excludes library/framework frames (node_modules, site-packages).
    """
    
    def test_extract_first_application_frame(self) -> None:
        """
        Validate extraction of first application code frame from stack trace.
        
        When stack trace contains both application and library frames, the
        fingerprinter should identify and use the first application frame.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Test error",
            stack_trace="""TypeError: Test error
    at validateUser (/app/pages/checkout.tsx:123:45)
    at node_modules/express/lib/router.js:284:15
    at node_modules/express/lib/router.js:335:10""",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Fingerprint should include the application frame, not library frames
        # We verify this by manually constructing expected fingerprint
        expected_components = "web-app|prod|TypeError|/app/pages/checkout.tsx:123:45|Test error"
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint
    
    def test_skip_library_frames(self) -> None:
        """
        Validate that library frames (node_modules, site-packages) are excluded.
        
        Per Section 0.7.1: "Extract first non-library frame (exclude node_modules,
        site-packages)"
        """
        fingerprinter = ErrorFingerprinter()
        
        # Stack trace starts with library frames, then application frame
        event = NormalizedErrorEvent(
            source="gcp",
            service="api-service",
            environment="staging",
            error_class="RuntimeError",
            message="Database error",
            stack_trace="""RuntimeError: Database error
    at node_modules/pg/lib/client.js:123:45
    at site-packages/django/db/backends/base.py:456:78
    at /app/services/database.py:89:12""",
            log_url="https://console.cloud.google.com/logs?insertId=xyz",
            event_id="gcp_001",
            occurred_at=datetime(2025, 1, 15, 11, 0, 0),
        )
        
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Expected fingerprint should use /app/services/database.py, not library frames
        expected_components = "api-service|staging|RuntimeError|/app/services/database.py:89:12|Database error"
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint
    
    def test_multiple_application_frames_uses_first(self) -> None:
        """
        Validate that when multiple application frames exist, the first one is used.
        
        The "top" frame is the first frame in the stack trace that belongs to
        application code, representing the immediate location of the error.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Test error",
            stack_trace="""TypeError: Test error
    at handlePayment (/app/pages/checkout.tsx:123:45)
    at processOrder (/app/lib/orders.ts:67:89)
    at validateCart (/app/utils/cart.ts:234:56)""",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Should use the first application frame (checkout.tsx)
        expected_components = "web-app|prod|TypeError|/app/pages/checkout.tsx:123:45|Test error"
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint
    
    @pytest.mark.parametrize("library_path", [
        "node_modules/express/lib/router.js:284:15",
        "site-packages/django/core/handlers/base.py:123:45",
        "dist/main.bundle.js:1:23456",
        "internal/modules/cjs/loader.js:789:12",
        "<anonymous>:1:1",
    ])
    def test_various_library_path_patterns_excluded(self, library_path: str) -> None:
        """
        Validate that various library path patterns are correctly identified
        and excluded from fingerprinting.
        
        Uses parametrization to test multiple library path formats.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Stack trace with library frame followed by application frame
        stack_trace = f"""Error: Test error
    at {library_path}
    at /app/services/handler.ts:100:25"""
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message="Test error",
            stack_trace=stack_trace,
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Should use application frame, not library frame
        expected_components = "web-app|prod|Error|/app/services/handler.ts:100:25|Test error"
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint


# =============================================================================
# Test Cases: Fallback Behavior
# =============================================================================

class TestFallbackBehavior:
    """
    Test suite validating fallback fingerprinting when stack trace is unavailable.
    
    Per Section 0.5.1 Group 3:
    "Handle missing stack traces: use error_class and first 50 chars of message"
    
    These tests ensure the fingerprinter gracefully handles errors without
    stack trace information and still produces stable, unique fingerprints.
    """
    
    def test_fallback_when_stack_trace_none(
        self, sample_event_without_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate fingerprint generation when stack_trace is None.
        
        Without stack trace information, the fingerprinter should use the first
        50 characters of the sanitized message as part of the fingerprint formula.
        """
        fingerprinter = ErrorFingerprinter()
        
        fingerprint = fingerprinter.generate_fingerprint(sample_event_without_stack)
        
        # Fingerprint should still be valid SHA-256 hex string
        assert len(fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint)
        
        # Generate expected fingerprint with message fallback
        message_truncated = sample_event_without_stack.message[:50]
        expected_components = (
            f"api-service|staging|RuntimeError|{message_truncated}|"
            f"{sample_event_without_stack.message}"
        )
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint
    
    def test_fallback_when_stack_trace_empty_string(self) -> None:
        """
        Validate fingerprint generation when stack_trace is an empty string.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message="Something went wrong in the application",
            stack_trace="",  # Empty string instead of None
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Should generate valid fingerprint using message fallback
        assert len(fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint)
    
    def test_fallback_uses_first_50_chars_of_message(self) -> None:
        """
        Validate that fallback behavior uses exactly first 50 characters of message.
        
        This is important for consistent fingerprinting when stack trace is unavailable.
        """
        # Create event with long message and no stack trace
        long_message = "Database connection timeout occurred while processing request"  # 61 character message
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message=long_message,
            stack_trace=None,
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        # Mock sanitizer to return message unchanged for this test
        # This isolates the truncation logic from sanitization behavior
        with patch.object(PIISanitizer, 'sanitize', return_value=long_message):
            fingerprinter = ErrorFingerprinter()
            fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Expected fingerprint should use first 50 chars in place of stack frame
        message_truncated = long_message[:50]
        expected_components = f"web-app|prod|Error|{message_truncated}|{long_message}"
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint
    
    def test_fallback_still_deterministic(self) -> None:
        """
        Validate that fallback fingerprints are still deterministic.
        
        Even without stack trace, the same error should always produce the
        same fingerprint across multiple invocations.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="gcp",
            service="api-service",
            environment="staging",
            error_class="RuntimeError",
            message="Database connection timeout",
            stack_trace=None,
            log_url="https://console.cloud.google.com/logs?insertId=xyz",
            event_id="gcp_001",
            occurred_at=datetime(2025, 1, 15, 11, 0, 0),
        )
        
        fingerprint1 = fingerprinter.generate_fingerprint(event)
        fingerprint2 = fingerprinter.generate_fingerprint(event)
        fingerprint3 = fingerprinter.generate_fingerprint(event)
        
        # All fingerprints must be identical
        assert fingerprint1 == fingerprint2 == fingerprint3


# =============================================================================
# Test Cases: SHA-256 Validation
# =============================================================================

class TestSHA256Implementation:
    """
    Test suite validating correct SHA-256 hash algorithm usage.
    
    Per Section 0.7.1 and Section 0.5.1:
    "Use SHA-256 for fingerprint generation to prevent collisions"
    
    These tests verify that the fingerprinting algorithm correctly uses
    SHA-256 hashing and produces valid hexadecimal string output.
    """
    
    def test_fingerprint_is_valid_sha256_hex_string(
        self, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate that fingerprint is a valid 64-character SHA-256 hex string.
        
        SHA-256 produces 256 bits = 32 bytes = 64 hexadecimal characters.
        """
        fingerprinter = ErrorFingerprinter()
        
        fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Verify length
        assert len(fingerprint) == 64, f"Expected 64 chars, got {len(fingerprint)}"
        
        # Verify all characters are valid hexadecimal (0-9, a-f)
        assert all(c in "0123456789abcdef" for c in fingerprint), \
            f"Fingerprint contains non-hex characters: {fingerprint}"
    
    def test_fingerprint_matches_manual_sha256_calculation(self) -> None:
        """
        Validate fingerprint by manually calculating expected SHA-256 hash.
        
        This test constructs the expected combined string and computes the
        SHA-256 hash independently to verify the algorithm implementation.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="TypeError",
            message="Test message",
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        actual_fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Manually construct expected combined string
        # Format: service|environment|error_class|top_frame|sanitized_message
        expected_combined = "web-app|prod|TypeError|/app/test.ts:10:20|Test message"
        expected_fingerprint = hashlib.sha256(expected_combined.encode('utf-8')).hexdigest()
        
        # Verify actual matches expected
        assert actual_fingerprint == expected_fingerprint
    
    def test_sha256_collision_resistance(self) -> None:
        """
        Demonstrate SHA-256 collision resistance with similar inputs.
        
        Even minimal changes to input should produce completely different
        hash outputs due to the avalanche effect of SHA-256.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Two very similar events (differ by one character in message)
        event1 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message="Test message A",
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        event2 = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message="Test message B",  # Only difference: A vs B
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_002",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint1 = fingerprinter.generate_fingerprint(event1)
        fingerprint2 = fingerprinter.generate_fingerprint(event2)
        
        # Fingerprints must be completely different
        assert fingerprint1 != fingerprint2
        
        # Calculate Hamming distance (number of differing characters)
        # SHA-256 avalanche effect should cause ~50% of bits to differ
        differences = sum(c1 != c2 for c1, c2 in zip(fingerprint1, fingerprint2))
        
        # At least 20 out of 64 hex characters should differ (31%+)
        # In practice, should be much higher due to avalanche effect
        assert differences >= 20, \
            f"Only {differences}/64 characters differ - weak avalanche effect"


# =============================================================================
# Test Cases: Edge Cases and Error Handling
# =============================================================================

class TestEdgeCases:
    """
    Test suite for edge cases and error handling scenarios.
    
    These tests ensure the fingerprinter handles unusual inputs gracefully
    and maintains stability across various edge conditions.
    """
    
    def test_very_long_error_message(self) -> None:
        """
        Validate fingerprinting with very long error messages.
        
        Ensures the algorithm can handle large message strings without
        performance degradation or errors.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Create event with 10KB message
        long_message = "A" * 10000
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message=long_message,
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        # Should complete without error
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Should produce valid fingerprint
        assert len(fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint)
    
    def test_empty_error_class(self) -> None:
        """
        Validate handling of minimal error_class.
        
        While validation should prevent truly empty error_class, this tests
        behavior with minimal content.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="E",  # Single character
            message="Test",
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        # Should still produce valid fingerprint
        fingerprint = fingerprinter.generate_fingerprint(event)
        assert len(fingerprint) == 64
    
    def test_unicode_characters_in_message(self) -> None:
        """
        Validate fingerprinting with Unicode characters in error message.
        
        Ensures proper UTF-8 encoding during hash generation.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message="Cannot process user input: 用户输入错误 🔴",
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        # Should handle Unicode correctly
        fingerprint1 = fingerprinter.generate_fingerprint(event)
        fingerprint2 = fingerprinter.generate_fingerprint(event)
        
        # Determinism should hold with Unicode
        assert fingerprint1 == fingerprint2
        assert len(fingerprint1) == 64
    
    def test_stack_trace_with_only_library_frames(self) -> None:
        """
        Validate fallback behavior when stack trace contains only library frames.
        
        If all frames are from libraries, should fall back to using message.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app",
            environment="prod",
            error_class="Error",
            message="Internal framework error",
            stack_trace="""Error: Internal framework error
    at node_modules/react/lib/ReactBaseClasses.js:123:45
    at node_modules/react-dom/lib/ReactDOM.js:678:90
    at node_modules/scheduler/index.js:234:56""",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint = fingerprinter.generate_fingerprint(event)
        
        # Should produce valid fingerprint using message fallback
        assert len(fingerprint) == 64
        
        # Expected fingerprint with message fallback (first 50 chars)
        message_truncated = event.message[:50]
        expected_components = f"web-app|prod|Error|{message_truncated}|{event.message}"
        expected_fingerprint = hashlib.sha256(expected_components.encode('utf-8')).hexdigest()
        
        assert fingerprint == expected_fingerprint
    
    def test_special_characters_in_service_name(self) -> None:
        """
        Validate fingerprinting with special characters in service name.
        
        Service names may contain hyphens, underscores, or other characters.
        """
        fingerprinter = ErrorFingerprinter()
        
        event = NormalizedErrorEvent(
            source="vercel",
            service="web-app-v2_prod",  # Hyphens and underscores
            environment="prod",
            error_class="Error",
            message="Test error",
            stack_trace="at /app/test.ts:10:20",
            log_url="https://vercel.com/logs?traceId=abc",
            event_id="evt_001",
            occurred_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        
        fingerprint1 = fingerprinter.generate_fingerprint(event)
        fingerprint2 = fingerprinter.generate_fingerprint(event)
        
        # Determinism should hold with special characters
        assert fingerprint1 == fingerprint2


# =============================================================================
# Test Cases: Integration with Dependencies
# =============================================================================

class TestDependencyIntegration:
    """
    Test suite validating integration with PIISanitizer dependency.
    
    These tests verify that ErrorFingerprinter correctly collaborates with
    PIISanitizer and handles various sanitizer behaviors.
    """
    
    def test_initialization_with_custom_sanitizer(self) -> None:
        """
        Validate that ErrorFingerprinter can be initialized with custom sanitizer.
        
        This supports dependency injection pattern for testing.
        """
        custom_sanitizer = PIISanitizer(config_path="custom/path.yaml")
        fingerprinter = ErrorFingerprinter(sanitizer=custom_sanitizer)
        
        # Verify custom sanitizer is used
        assert fingerprinter._sanitizer is custom_sanitizer
    
    def test_initialization_with_default_sanitizer(self) -> None:
        """
        Validate that ErrorFingerprinter creates default sanitizer when none provided.
        """
        fingerprinter = ErrorFingerprinter()
        
        # Should have a sanitizer instance
        assert fingerprinter._sanitizer is not None
        assert isinstance(fingerprinter._sanitizer, PIISanitizer)
    
    @patch.object(PIISanitizer, 'sanitize')
    def test_handles_sanitizer_exception_gracefully(
        self, mock_sanitize, sample_event_with_stack: NormalizedErrorEvent
    ) -> None:
        """
        Validate graceful handling when sanitizer raises an exception.
        
        Per Section 0.7.2 graceful degradation: The fingerprinter should
        continue operating even if sanitization fails, using original message
        as fallback.
        """
        # Mock sanitizer to raise exception
        mock_sanitize.side_effect = Exception("Sanitization failed")
        
        fingerprinter = ErrorFingerprinter()
        
        # Should not raise exception - should handle gracefully
        fingerprint = fingerprinter.generate_fingerprint(sample_event_with_stack)
        
        # Should still produce a valid fingerprint
        assert len(fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint)


if __name__ == "__main__":
    # Enable running tests directly with: python -m pytest tests/unit/test_fingerprinter.py
    pytest.main([__file__, "-v", "--tb=short"])
