"""
Error Fingerprinting Service

This module implements the ErrorFingerprinter service that generates stable,
consistent hash identifiers for error grouping. The fingerprinting algorithm
ensures identical errors produce the same fingerprint for proper Jira issue
deduplication across multiple occurrences.

The fingerprint generation follows the CRITICAL formula from Section 0.7.1:
    fingerprint = hash(service + env + error_class + top_stack_frame + sanitized_message)

Algorithm steps:
1. Extract top stack frame from stack trace (first non-library frame)
2. Sanitize error message to remove PII before hashing
3. Combine components: service|environment|error_class|stack_frame|message
4. Generate SHA-256 hash for stable 64-character fingerprint

Key Design Principles:
- Deterministic: Identical errors always produce identical fingerprints
- Collision-resistant: SHA-256 provides strong hash uniqueness
- PII-safe: Message sanitization occurs before hashing
- Library-aware: Excludes node_modules and site-packages from stack frames
- Fallback-capable: Gracefully handles missing stack traces

User Example from Section 0.7.1:
    "Fingerprint stability is CRITICAL. Use exactly this formula:
     hash(service + env + error_class + top_stack_frame + sanitized_message)"

Performance Considerations:
- Pre-compiled regex patterns for stack frame extraction
- Single-pass message sanitization
- SHA-256 optimized for speed and security
"""

import hashlib
import logging
import re
from typing import Optional

from src.models.error_event import NormalizedErrorEvent
from src.services.sanitizer import PIISanitizer

# Initialize logger for structured logging per Section 0.7.2 observability requirement
logger = logging.getLogger(__name__)


class ErrorFingerprinter:
    """
    Error fingerprinting service for generating stable hash identifiers.

    This class implements the critical fingerprinting algorithm that ensures
    identical errors from multiple sources are grouped together in Jira. The
    fingerprint must remain stable across occurrences to enable proper issue
    deduplication and frequency tracking.

    The algorithm extracts the top (first) non-library stack frame, sanitizes
    the error message to remove PII, combines error attributes in a specific
    order, and generates a SHA-256 hash.

    Attributes:
        _sanitizer: PIISanitizer instance for removing PII from messages
        _stack_frame_pattern: Compiled regex for extracting stack frames
        _library_path_patterns: Compiled regexes for identifying library frames

    Thread Safety:
        This class is thread-safe after initialization. Multiple concurrent
        calls to generate_fingerprint() are safe and will produce consistent
        results for identical input events.

    Usage Example:
        >>> fingerprinter = ErrorFingerprinter()
        >>> fingerprint = fingerprinter.generate_fingerprint(event)
        >>> print(f"Fingerprint: {fingerprint}")  # 64-character SHA-256 hex string
        Fingerprint: a3f5b9c8d2e1f4a7b6c5d8e9f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0
    """

    def __init__(self, sanitizer: Optional[PIISanitizer] = None):
        """
        Initialize ErrorFingerprinter with PII sanitization capability.

        Args:
            sanitizer: PIISanitizer instance for message sanitization.
                      If None, creates a new PIISanitizer with default config.
                      Allows dependency injection for testing.

        Note:
            The sanitizer is initialized with default configuration path
            (config/sanitization_patterns.yaml) if not provided. This ensures
            PII is removed before fingerprint generation per Section 0.7.1
            requirement to apply sanitization before hashing.
        """
        self._sanitizer = sanitizer or PIISanitizer()

        # Pre-compile regex pattern for stack frame extraction
        # Pattern matches common JavaScript/TypeScript stack trace format:
        # "at functionName (/path/to/file.ts:123:45)"
        # Per Section 0.5.1: Use pattern r'at ([\w/<>\.]+):(\d+):(\d+)'
        self._stack_frame_pattern = re.compile(
            r'at\s+(?:[\w\.<>]+\s+\()?([^\s\)]+):(\d+):(\d+)\)?',
            re.MULTILINE
        )

        # Patterns to identify library/framework code that should be excluded
        # from fingerprints to focus on application-specific stack frames
        # Patterns match with or without leading/trailing slashes
        self._library_path_patterns = [
            re.compile(r'node_modules'),  # Matches anywhere in path
            re.compile(r'site-packages'),  # Matches anywhere in path
            re.compile(r'dist/'),  # Distribution directory
            re.compile(r'\\dist\\'),  # Distribution directory (Windows)
            re.compile(r'<anonymous>'),  # Anonymous functions
            re.compile(r'internal/'),  # Node.js internal modules
            re.compile(r'internal\\'),  # Node.js internal modules (Windows)
        ]

        logger.info(
            "ErrorFingerprinter initialized successfully",
            extra={
                "sanitizer_pattern_count": self._sanitizer.get_pattern_count(),
                "stack_frame_pattern": self._stack_frame_pattern.pattern,
            },
        )

    def _extract_top_stack_frame(self, stack_trace: str) -> Optional[str]:
        """
        Extract the first non-library stack frame from error stack trace.

        This method parses the stack trace to find the first frame that belongs
        to application code (not node_modules, site-packages, or other library
        directories). This ensures fingerprints are based on where the error
        occurred in application code, not in framework internals.

        Per Section 0.7.1 requirement #2:
        "Top stack frame extraction: use the FIRST non-library frame
         (exclude node_modules, site-packages)"

        Args:
            stack_trace: Full error stack trace string from error event

        Returns:
            Stack frame string in format "file:line:col" or None if no
            application frames found. Examples:
            - "/app/pages/checkout.tsx:123:45"
            - "src/services/payment.js:67:12"
            - None (if only library frames present)

        Algorithm:
        1. Use regex to find all stack frames in trace
        2. For each frame, check if path contains library indicators
        3. Return first frame that doesn't match library patterns
        4. Return None if no application frames found

        Performance:
            O(n) where n is number of stack frames, typically 10-50 frames.
            Early termination on first match for optimal performance.
        """
        if not stack_trace:
            return None

        # Find all stack frame matches in the trace
        matches = self._stack_frame_pattern.finditer(stack_trace)

        for match in matches:
            # Extract file path and position from regex groups
            file_path = match.group(1)
            line_number = match.group(2)
            column_number = match.group(3)

            # Check if this frame is from library/framework code
            is_library_frame = any(
                pattern.search(file_path) for pattern in self._library_path_patterns
            )

            if not is_library_frame:
                # Found first application code frame - use for fingerprint
                frame_identifier = f"{file_path}:{line_number}:{column_number}"

                logger.debug(
                    "Extracted top application stack frame",
                    extra={
                        "frame": frame_identifier,
                        "file_path": file_path,
                        "line": line_number,
                        "column": column_number,
                    },
                )

                return frame_identifier

        # No application frames found - all frames were library code
        logger.debug(
            "No application stack frames found - all frames from libraries",
            extra={"stack_trace_length": len(stack_trace)},
        )

        return None

    def _create_fingerprint_components(
        self, event: NormalizedErrorEvent
    ) -> tuple[str, str, Optional[str]]:
        """
        Create and sanitize the components used for fingerprint generation.

        This helper method extracts the necessary components from the error event,
        applies PII sanitization to the message, and extracts the top stack frame.
        These components are then combined in a specific order for hashing.

        Args:
            event: Normalized error event with all required fields

        Returns:
            Tuple of (combined_string, sanitized_message, top_frame):
            - combined_string: Final string to be hashed (service|env|class|frame|msg)
            - sanitized_message: Message with PII removed (for logging/debugging)
            - top_frame: Extracted stack frame or None if unavailable

        Note:
            The combined string uses pipe (|) as delimiter to ensure component
            boundaries are clear and prevent ambiguous combinations that could
            cause fingerprint collisions.
        """
        # Sanitize message BEFORE hashing per Section 0.7.1 critical directive:
        # "Sanitization MUST occur before hashing to ensure consistent
        #  fingerprints despite variable data"
        sanitized_message = self._sanitizer.sanitize(event.message)

        # Extract top stack frame if stack trace is available
        top_frame = None
        if event.stack_trace:
            top_frame = self._extract_top_stack_frame(event.stack_trace)

        # Build combined string using exact formula from Section 0.7.1:
        # hash(service + env + error_class + top_stack_frame + sanitized_message)
        #
        # Use pipe delimiter to prevent component boundary ambiguity
        # Example: "web-app|prod|TypeError|/app/checkout.tsx:123:45|Cannot read [ID]"
        components = [
            event.service,
            event.environment,
            event.error_class,
        ]

        # Add stack frame if available, otherwise use placeholder
        if top_frame:
            components.append(top_frame)
        else:
            # Use truncated message as fallback per Section 0.5.1:
            # "Handle missing stack traces: use error_class and first 50 chars of message"
            components.append(sanitized_message[:50])

        # Always append full sanitized message for maximum specificity
        components.append(sanitized_message)

        combined = "|".join(components)

        return combined, sanitized_message, top_frame

    def generate_fingerprint(self, event: NormalizedErrorEvent) -> str:
        """
        Generate stable SHA-256 fingerprint for error event grouping.

        This is the main public method that implements the CRITICAL fingerprinting
        algorithm from Section 0.7.1. It produces a deterministic 64-character
        hexadecimal fingerprint that remains stable across identical error
        occurrences, enabling proper Jira issue deduplication.

        Algorithm (per Section 0.5.1 Group 3):
        1. Sanitize error message using PIISanitizer to remove PII
        2. Extract top stack frame using regex pattern matching
        3. Filter out library frames (node_modules, site-packages)
        4. Combine: service|environment|error_class|top_frame|sanitized_message
        5. Generate SHA-256 hash: hashlib.sha256(combined.encode()).hexdigest()

        Fallback Behavior (when stack trace missing):
        - Use error_class + first 50 characters of sanitized message
        - Still produces stable fingerprint, just less specific
        - Documented in Section 0.7.1: "Handle missing stack traces gracefully"

        Args:
            event: NormalizedErrorEvent with error details and optional stack trace

        Returns:
            64-character hexadecimal SHA-256 fingerprint string
            Example: "a3f5b9c8d2e1f4a7b6c5d8e9f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0"

        Raises:
            This method does not raise exceptions. If errors occur during
            processing (e.g., sanitization failure), the original message
            is used to ensure fingerprint generation always succeeds.

        Determinism Guarantee:
            Identical error events (same service, environment, error_class,
            stack location, and message content after PII removal) will
            ALWAYS produce the same fingerprint. This is critical for:
            - Jira issue deduplication
            - Frequency counting accuracy
            - Severity threshold evaluation

        Performance:
            Typical execution time: <1ms
            - Regex matching: O(n) where n = stack trace length
            - Sanitization: O(m) where m = message length
            - SHA-256 hashing: O(k) where k = combined string length

        Example:
            >>> event = NormalizedErrorEvent(
            ...     source='vercel',
            ...     service='web-app',
            ...     environment='prod',
            ...     error_class='TypeError',
            ...     message='Cannot read property x of user_id=12345',
            ...     stack_trace='at checkout (/app/pages/checkout.tsx:123:45)',
            ...     log_url='https://vercel.com/logs?trace=abc',
            ...     event_id='evt_123',
            ...     occurred_at=datetime.now()
            ... )
            >>> fingerprint = fingerprinter.generate_fingerprint(event)
            >>> print(len(fingerprint))  # Always 64 characters
            64
            >>> print(fingerprint[:16])  # First 16 chars
            a3f5b9c8d2e1f4a7
        """
        try:
            # Create and sanitize fingerprint components
            combined, sanitized_message, top_frame = self._create_fingerprint_components(event)

            # Generate SHA-256 hash per Section 0.7.1 requirement:
            # "Use SHA-256 for fingerprint generation to prevent collisions"
            fingerprint = hashlib.sha256(combined.encode('utf-8')).hexdigest()

            # Log successful fingerprint generation with context for observability
            logger.info(
                "Generated error fingerprint",
                extra={
                    "fingerprint": fingerprint,
                    "event_id": event.event_id,
                    "service": event.service,
                    "environment": event.environment,
                    "error_class": event.error_class,
                    "top_frame": top_frame or "fallback_to_message",
                    "has_stack_trace": event.stack_trace is not None,
                    "sanitized_message_length": len(sanitized_message),
                    "combined_length": len(combined),
                },
            )

            return fingerprint

        except Exception as e:
            # Graceful degradation: if fingerprint generation fails for any reason,
            # create a basic fingerprint from available data to ensure processing
            # continues. This prevents a single malformed event from blocking
            # the entire error processing pipeline.
            logger.error(
                "Error during fingerprint generation - using fallback",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "event_id": event.event_id,
                    "service": event.service,
                    "environment": event.environment,
                    "error_class": event.error_class,
                },
            )

            # Fallback fingerprint using basic attributes without sanitization
            # This ensures every error gets a fingerprint even if processing fails
            fallback_string = f"{event.service}|{event.environment}|{event.error_class}|{event.message[:100]}"
            fallback_fingerprint = hashlib.sha256(fallback_string.encode('utf-8')).hexdigest()

            logger.warning(
                "Generated fallback fingerprint",
                extra={
                    "fingerprint": fallback_fingerprint,
                    "event_id": event.event_id,
                },
            )

            return fallback_fingerprint
