"""
PII Sanitization Service

This module provides PII (Personally Identifiable Information) sanitization
functionality for error messages and stack traces. It removes sensitive data
such as email addresses, UUIDs, authentication tokens, and numeric IDs before
fingerprint generation and Jira transmission to ensure compliance with data
privacy requirements.

The sanitization patterns are loaded from config/sanitization_patterns.yaml
and compiled regex patterns are cached in memory for optimal performance.

Per Section 0.7.4: Sanitization must occur in two places:
1. Before fingerprint generation (ensures grouping stability)
2. Before sending to Jira (ensures no PII in tickets)

User Examples from Section 0.7.4:
- Email addresses: user@example.com → [EMAIL]
- UUIDs: 550e8400-e29b-41d4-a716-446655440000 → [UUID]
- Numeric IDs: user_id=12345 → user_id=[ID]
- Tokens: Bearer eyJhbGc... → Bearer [TOKEN]
"""

import logging
import re
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

import yaml

# Initialize logger for structured logging
logger = logging.getLogger(__name__)


class PIISanitizer:
    """
    PII sanitization service that removes sensitive data from error messages
    and stack traces using configurable regex patterns.

    This class implements a configuration-driven approach where all PII detection
    patterns are loaded from an external YAML file, enabling pattern updates
    without code changes. Compiled regex patterns are cached in memory for
    performance optimization.

    Attributes:
        _patterns: List of compiled (regex_pattern, replacement) tuples cached in memory
        _patterns_loaded: Boolean flag indicating if patterns have been loaded
        _config_path: Path to the YAML configuration file

    Thread Safety:
        This class is thread-safe for read operations (sanitize) after initial
        pattern loading. Pattern loading should occur once at initialization.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize PIISanitizer with optional configuration file path.

        Args:
            config_path: Path to sanitization_patterns.yaml file.
                        Defaults to 'config/sanitization_patterns.yaml'
                        relative to repository root.
        """
        self._patterns: List[Tuple[Pattern, str]] = []
        self._patterns_loaded: bool = False
        self._config_path: str = config_path or "config/sanitization_patterns.yaml"

        # Attempt to load patterns at initialization
        try:
            self.load_patterns(self._config_path)
            logger.info(
                "PIISanitizer initialized successfully",
                extra={
                    "config_path": self._config_path,
                    "pattern_count": len(self._patterns),
                },
            )
        except Exception as e:
            logger.error(
                "Failed to load sanitization patterns at initialization",
                extra={"config_path": self._config_path, "error": str(e)},
            )
            # Continue with empty patterns list - sanitize() will be no-op
            self._patterns = []

    def sanitize(self, text: str) -> str:
        """
        Apply all configured regex patterns to remove PII from input text.

        This method sequentially applies each compiled regex pattern to the input
        text, replacing matches with their corresponding placeholder tokens. The
        order of pattern application follows the order defined in the YAML
        configuration file.

        Preserves stack trace structure while removing sensitive values to maintain
        debugging utility while protecting privacy.

        Args:
            text: Input text that may contain PII (error message, stack trace, etc.)

        Returns:
            Sanitized text with all PII replaced by placeholder tokens:
            - Email addresses → [EMAIL]
            - UUIDs → [UUID]
            - Numeric IDs → [ID]
            - Authentication tokens → [TOKEN]

        Examples:
            >>> sanitizer = PIISanitizer()
            >>> sanitizer.sanitize("Error for user@example.com")
            'Error for [EMAIL]'

            >>> sanitizer.sanitize("Request failed: user_id=12345")
            'Request failed: user_id=[ID]'

            >>> sanitizer.sanitize("UUID: 550e8400-e29b-41d4-a716-446655440000")
            'UUID: [UUID]'

        Performance:
            Uses pre-compiled regex patterns cached in memory for O(n) performance
            where n is the length of input text. Multiple pattern applications are
            necessary to handle diverse PII types.
        """
        if not text:
            return text

        if not self._patterns_loaded or not self._patterns:
            logger.warning(
                "Sanitization attempted with no patterns loaded - returning original text",
                extra={"text_length": len(text), "patterns_loaded": self._patterns_loaded},
            )
            return text

        sanitized = text
        replacements_made = 0

        try:
            # Apply each pattern sequentially to catch all PII types
            for pattern, replacement in self._patterns:
                before = sanitized
                sanitized = pattern.sub(replacement, sanitized)

                # Count replacements for observability
                if sanitized != before:
                    replacements_made += 1

            logger.debug(
                "Text sanitization completed",
                extra={
                    "original_length": len(text),
                    "sanitized_length": len(sanitized),
                    "replacements_made": replacements_made,
                    "patterns_applied": len(self._patterns),
                },
            )

        except Exception as e:
            logger.error(
                "Error during text sanitization - returning original text",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "text_length": len(text),
                },
            )
            # Fail open: return original text rather than crashing
            # This ensures service continues operating even if sanitization fails
            return text

        return sanitized

    def _compile_patterns(self, patterns_config: list) -> Tuple[List[Tuple[Pattern, str]], List[Tuple[str, str]]]:
        """
        Compile regex patterns from configuration list.

        Args:
            patterns_config: List of pattern dictionaries from YAML configuration

        Returns:
            Tuple of (compiled_patterns, raw_patterns) lists

        Raises:
            ValueError: If pattern structure is invalid or regex compilation fails
        """
        compiled_patterns: List[Tuple[Pattern, str]] = []
        raw_patterns: List[Tuple[str, str]] = []

        for idx, pattern_obj in enumerate(patterns_config):
            if not isinstance(pattern_obj, dict):
                raise ValueError(f"Pattern at index {idx} is not a dictionary")

            if "pattern" not in pattern_obj or "replacement" not in pattern_obj:
                raise ValueError(f"Pattern at index {idx} missing 'pattern' or 'replacement' key")

            pattern_str = pattern_obj["pattern"]
            replacement_str = pattern_obj["replacement"]

            try:
                # Compile regex pattern with IGNORECASE flag for email matching
                compiled_pattern = re.compile(pattern_str, re.IGNORECASE)
                compiled_patterns.append((compiled_pattern, replacement_str))
                raw_patterns.append((pattern_str, replacement_str))

            except re.error as e:
                raise ValueError(f"Invalid regex pattern at index {idx}: '{pattern_str}' - {str(e)}")

        return compiled_patterns, raw_patterns

    def load_patterns(self, yaml_path: str) -> List[Tuple[str, str]]:
        """
        Load and compile PII detection regex patterns from YAML configuration file.

        This method reads the sanitization_patterns.yaml file, validates its structure,
        compiles all regex patterns for performance, and caches them in memory. The
        YAML file should contain a 'patterns' key with a list of pattern objects,
        each having 'pattern' (regex string) and 'replacement' (placeholder) keys.

        Expected YAML structure:
            patterns:
              - pattern: '\\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\b'
                replacement: '[UUID]'
              - pattern: '\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b'
                replacement: '[EMAIL]'
              - pattern: '\\b(?:user_id|userId|id)[:=]\\s*\\d+'
                replacement: '\\1=[ID]'

        Args:
            yaml_path: Path to the YAML configuration file containing pattern definitions.
                      Can be absolute or relative to current working directory.

        Returns:
            List of (pattern_string, replacement_string) tuples loaded from YAML.
            Note: The internal cache stores compiled Pattern objects, not strings.

        Raises:
            FileNotFoundError: If the YAML file does not exist at the specified path
            ValueError: If the YAML structure is invalid or patterns fail to compile
            yaml.YAMLError: If the YAML file cannot be parsed

        Side Effects:
            Updates self._patterns with compiled regex patterns
            Sets self._patterns_loaded to True on success

        Performance:
            Pattern compilation occurs once at load time. Subsequent sanitize()
            calls use cached compiled patterns for optimal performance.
        """
        config_file = Path(yaml_path)

        # Validate file existence
        if not config_file.exists():
            error_msg = f"Sanitization patterns file not found: {yaml_path}"
            logger.error(
                "Configuration file missing",
                extra={"yaml_path": yaml_path, "resolved_path": str(config_file.resolve())},
            )
            raise FileNotFoundError(error_msg)

        try:
            # Load YAML configuration with safe_load to prevent code execution
            yaml_content = config_file.read_text(encoding="utf-8")
            config = yaml.safe_load(yaml_content)

            if not config:
                raise ValueError("YAML configuration is empty")

            if "patterns" not in config:
                raise ValueError("YAML configuration missing required 'patterns' key")

            patterns_config = config["patterns"]

            if not isinstance(patterns_config, list):
                raise ValueError("'patterns' must be a list of pattern objects")

            # Compile all patterns using helper method
            compiled_patterns, raw_patterns = self._compile_patterns(patterns_config)

            # Update instance state atomically
            self._patterns = compiled_patterns
            self._patterns_loaded = True

            logger.info(
                "Sanitization patterns loaded and compiled successfully",
                extra={
                    "yaml_path": yaml_path,
                    "pattern_count": len(compiled_patterns),
                    "patterns": [p[0] for p in raw_patterns],  # Log pattern strings
                },
            )

            # Return raw patterns for caller inspection (not compiled objects)
            return raw_patterns

        except yaml.YAMLError as e:
            error_msg = f"Failed to parse YAML file: {yaml_path}"
            logger.error(
                "YAML parsing error",
                extra={"yaml_path": yaml_path, "error": str(e), "error_type": "YAMLError"},
            )
            raise ValueError(f"{error_msg}: {str(e)}")

        except Exception as e:
            logger.error(
                "Unexpected error loading sanitization patterns",
                extra={
                    "yaml_path": yaml_path,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            raise

    def get_pattern_count(self) -> int:
        """
        Get the number of loaded sanitization patterns.

        Returns:
            Number of compiled regex patterns currently cached in memory.

        Note:
            This method is useful for health checks and monitoring to verify
            patterns were loaded successfully at initialization.
        """
        return len(self._patterns)

    def reload_patterns(self) -> int:
        """
        Reload patterns from the configuration file.

        This method enables hot-reload of sanitization patterns without service
        restart, supporting the SIGHUP handler use case mentioned in Section 0.1.1.

        Returns:
            Number of patterns loaded after reload

        Raises:
            FileNotFoundError: If configuration file no longer exists
            ValueError: If configuration is invalid

        Note:
            This method is thread-safe but may cause brief inconsistency if
            called while sanitize() operations are in progress. For production
            use, coordinate reloads during low-traffic periods.
        """
        self._patterns_loaded = False
        self.load_patterns(self._config_path)
        return len(self._patterns)
