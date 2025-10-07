"""
Unit Tests for PIISanitizer Service

This module provides comprehensive unit tests for the PIISanitizer class that
removes personally identifiable information from error messages and stack traces.
Tests validate pattern matching for emails, UUIDs, numeric IDs, tokens, and
edge case handling per Agent Action Plan Section 0.5.1 Group 3.

Coverage Requirements:
- Minimum 80% code coverage, target 90%+ (per Section 0.7.2)
- All PII patterns validated (email, UUID, ID, token)
- Edge cases handled (empty, None, malformed inputs)
- Configuration loading tested (valid YAML, errors, missing file)
- Deterministic output verified (same input → same output)
- Stack trace structure preservation validated

Test Categories:
1. Email Sanitization Tests
2. UUID Sanitization Tests
3. Numeric ID Sanitization Tests
4. Token Sanitization Tests
5. Pattern Loading Tests
6. Edge Case Tests
7. Deterministic Output Tests
8. Stack Trace Preservation Tests
9. Integration Tests
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch, mock_open

import pytest
import yaml

from src.services.sanitizer import PIISanitizer


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def valid_patterns_config() -> List[Dict[str, str]]:
    """
    Fixture providing valid sanitization pattern configuration.
    
    Returns valid YAML-compatible pattern list matching the structure
    defined in config/sanitization_patterns.yaml per Section 0.5.1.
    
    Note: Patterns use capturing groups () not non-capturing groups (?:)
    to enable backreferences in replacement strings like \1=[ID].
    
    Patterns are applied in order, with more specific patterns first to ensure
    correct matching (e.g., braced IDs before simple IDs).
    """
    return [
        {
            "pattern": r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            "replacement": "[UUID]"
        },
        {
            "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "replacement": "[EMAIL]"
        },
        {
            # Match IDs in curly braces first (more specific): user_id={12345}
            "pattern": r"\b(user_id|userId|id)\s*=\s*\{\d+\}",
            "replacement": r"\1={[ID]}"
        },
        {
            # Match IDs with = or : separator, capturing identifier and normalizing spaces
            "pattern": r"\b(user_id|userId|id)\s*[:=]\s*\d+",
            "replacement": r"\1=[ID]"
        },
        {
            "pattern": r"Bearer\s+[A-Za-z0-9_\-\.]+",
            "replacement": "Bearer [TOKEN]"
        },
        {
            "pattern": r"\b(token|auth_token|api_key)[:=]\s*['\"]?[A-Za-z0-9_\-\.]+['\"]?",
            "replacement": r"\1=[TOKEN]"
        }
    ]


@pytest.fixture
def valid_yaml_content(valid_patterns_config: List[Dict[str, str]]) -> str:
    """
    Fixture providing valid YAML content for pattern configuration.
    
    Returns YAML string with 'patterns' key containing pattern list.
    """
    return yaml.dump({"patterns": valid_patterns_config})


@pytest.fixture
def mock_yaml_file(tmp_path: Path, valid_yaml_content: str) -> Path:
    """
    Fixture creating temporary YAML file with valid patterns.
    
    Uses pytest's tmp_path fixture for isolated file system testing.
    """
    yaml_file = tmp_path / "test_patterns.yaml"
    yaml_file.write_text(valid_yaml_content, encoding="utf-8")
    return yaml_file


@pytest.fixture
def sanitizer_with_patterns(mock_yaml_file: Path) -> PIISanitizer:
    """
    Fixture providing PIISanitizer instance with loaded patterns.
    
    Uses temporary YAML file to avoid dependency on actual config file.
    """
    return PIISanitizer(config_path=str(mock_yaml_file))


@pytest.fixture
def sanitizer_no_patterns() -> PIISanitizer:
    """
    Fixture providing PIISanitizer instance without loaded patterns.
    
    Mocks file operations to simulate initialization failure.
    """
    with patch("pathlib.Path.exists", return_value=False):
        return PIISanitizer(config_path="/nonexistent/path.yaml")


# =============================================================================
# EMAIL SANITIZATION TESTS
# =============================================================================

class TestEmailSanitization:
    """Test suite for email address sanitization patterns."""

    @pytest.mark.parametrize("input_text,expected_output", [
        # Single email addresses
        ("Error for user@example.com", "Error for [EMAIL]"),
        ("Contact: admin@test.org", "Contact: [EMAIL]"),
        ("Email john.doe@company.co.uk", "Email [EMAIL]"),
        
        # Multiple emails
        (
            "Users: alice@example.com and bob@test.com",
            "Users: [EMAIL] and [EMAIL]"
        ),
        
        # Email in different contexts
        ("user@example.com failed login", "[EMAIL] failed login"),
        ("Sent to support@mycompany.io", "Sent to [EMAIL]"),
        
        # Complex email formats
        ("Contact: first.last+tag@sub.domain.com", "Contact: [EMAIL]"),
        ("Email: user_name123@test-domain.org", "Email: [EMAIL]"),
    ])
    def test_email_sanitization_patterns(
        self,
        sanitizer_with_patterns: PIISanitizer,
        input_text: str,
        expected_output: str
    ):
        """
        Test email address sanitization with various formats.
        
        Validates that user@example.com → [EMAIL] per Section 0.7.4.
        """
        result = sanitizer_with_patterns.sanitize(input_text)
        assert result == expected_output

    def test_email_case_insensitivity(self, sanitizer_with_patterns: PIISanitizer):
        """Test that email matching is case-insensitive."""
        input_text = "Contact: User@EXAMPLE.COM and admin@Test.Org"
        result = sanitizer_with_patterns.sanitize(input_text)
        assert "[EMAIL]" in result
        assert "User@EXAMPLE.COM" not in result
        assert "admin@Test.Org" not in result

    def test_email_preserves_surrounding_text(self, sanitizer_with_patterns: PIISanitizer):
        """Test that only email is replaced, surrounding text preserved."""
        input_text = "Error: user@example.com caused issue at line 42"
        result = sanitizer_with_patterns.sanitize(input_text)
        assert result.startswith("Error: [EMAIL]")
        assert result.endswith("caused issue at line 42")
        assert "user@example.com" not in result


# =============================================================================
# UUID SANITIZATION TESTS
# =============================================================================

class TestUUIDSanitization:
    """Test suite for UUID sanitization patterns."""

    @pytest.mark.parametrize("input_text,expected_output", [
        # Standard UUID v4 format (from Section 0.7.4 example)
        (
            "UUID: 550e8400-e29b-41d4-a716-446655440000",
            "UUID: [UUID]"
        ),
        
        # Multiple UUIDs
        (
            "ID1: 550e8400-e29b-41d4-a716-446655440000 and ID2: 123e4567-e89b-12d3-a456-426614174000",
            "ID1: [UUID] and ID2: [UUID]"
        ),
        
        # UUID in error messages
        (
            "Failed to process request abc12345-def6-7890-abcd-ef1234567890",
            "Failed to process request [UUID]"
        ),
        
        # UUID with different casings
        (
            "Request ID: ABCDEF12-3456-7890-ABCD-EF1234567890",
            "Request ID: [UUID]"
        ),
        (
            "Trace: abcdef12-3456-7890-abcd-ef1234567890",
            "Trace: [UUID]"
        ),
    ])
    def test_uuid_sanitization_patterns(
        self,
        sanitizer_with_patterns: PIISanitizer,
        input_text: str,
        expected_output: str
    ):
        """
        Test UUID sanitization with various formats.
        
        Validates that 550e8400-e29b-41d4-a716-446655440000 → [UUID]
        per Section 0.7.4.
        """
        result = sanitizer_with_patterns.sanitize(input_text)
        assert result == expected_output

    def test_uuid_in_stack_trace(self, sanitizer_with_patterns: PIISanitizer):
        """Test UUID sanitization within stack trace context."""
        stack_trace = """
        File "/app/service.py", line 42, in process_request
            request_id = '550e8400-e29b-41d4-a716-446655440000'
        RequestError: Failed to process request
        """
        result = sanitizer_with_patterns.sanitize(stack_trace)
        assert "[UUID]" in result
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        # Verify stack trace structure preserved
        assert "File \"/app/service.py\", line 42" in result
        assert "RequestError: Failed to process request" in result


# =============================================================================
# NUMERIC ID SANITIZATION TESTS
# =============================================================================

class TestNumericIDSanitization:
    """Test suite for numeric ID sanitization patterns."""

    @pytest.mark.parametrize("input_text,expected_output", [
        # user_id patterns (from Section 0.7.4 example)
        ("user_id=12345", "user_id=[ID]"),
        ("user_id: 67890", "user_id=[ID]"),
        ("user_id = 99999", "user_id=[ID]"),
        
        # userId camelCase patterns
        ("userId=12345", "userId=[ID]"),
        ("userId: 67890", "userId=[ID]"),
        
        # Generic id patterns
        ("id=54321", "id=[ID]"),
        ("id: 11111", "id=[ID]"),
        
        # Multiple IDs
        (
            "Processing user_id=12345 with order_id=67890",
            "Processing user_id=[ID] with order_id=67890"  # order_id not in pattern
        ),
    ])
    def test_numeric_id_sanitization_patterns(
        self,
        sanitizer_with_patterns: PIISanitizer,
        input_text: str,
        expected_output: str
    ):
        """
        Test numeric ID sanitization with various formats.
        
        Validates that user_id=12345 → user_id=[ID] per Section 0.7.4.
        """
        result = sanitizer_with_patterns.sanitize(input_text)
        assert result == expected_output

    def test_id_in_error_message(self, sanitizer_with_patterns: PIISanitizer):
        """Test ID sanitization in error message context."""
        error_msg = "Database error: user_id=12345 not found in table users"
        result = sanitizer_with_patterns.sanitize(error_msg)
        assert "user_id=[ID]" in result
        assert "12345" not in result
        assert "not found in table users" in result


# =============================================================================
# TOKEN SANITIZATION TESTS
# =============================================================================

class TestTokenSanitization:
    """Test suite for authentication token sanitization patterns."""

    @pytest.mark.parametrize("input_text,expected_output", [
        # Bearer tokens (from Section 0.7.4 example)
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "Authorization: Bearer [TOKEN]"
        ),
        (
            "Bearer abc123def456",
            "Bearer [TOKEN]"
        ),
        
        # token parameter patterns
        ("token=abc123def456", "token=[TOKEN]"),
        ("token: xyz789", "token=[TOKEN]"),
        ("auth_token=secret123", "auth_token=[TOKEN]"),
        ("api_key='sk_live_123456'", "api_key=[TOKEN]"),
        ("api_key=\"pk_test_789012\"", "api_key=[TOKEN]"),
    ])
    def test_token_sanitization_patterns(
        self,
        sanitizer_with_patterns: PIISanitizer,
        input_text: str,
        expected_output: str
    ):
        """
        Test authentication token sanitization with various formats.
        
        Validates that Bearer eyJhbGc... → Bearer [TOKEN] per Section 0.7.4.
        """
        result = sanitizer_with_patterns.sanitize(input_text)
        assert result == expected_output

    def test_jwt_token_sanitization(self, sanitizer_with_patterns: PIISanitizer):
        """Test full JWT token sanitization."""
        jwt_token = (
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        error_msg = f"Authentication failed: {jwt_token}"
        result = sanitizer_with_patterns.sanitize(error_msg)
        assert "Bearer [TOKEN]" in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result


# =============================================================================
# PATTERN LOADING TESTS
# =============================================================================

class TestPatternLoading:
    """Test suite for YAML pattern configuration loading."""

    def test_load_patterns_success(self, mock_yaml_file: Path):
        """Test successful pattern loading from valid YAML file."""
        sanitizer = PIISanitizer(config_path=str(mock_yaml_file))
        assert sanitizer._patterns_loaded is True
        assert len(sanitizer._patterns) > 0
        assert sanitizer.get_pattern_count() == 6  # From fixture (UUID, EMAIL, ID-braced, ID, Bearer, token)

    def test_load_patterns_file_not_found(self, tmp_path: Path):
        """Test FileNotFoundError when YAML file doesn't exist."""
        nonexistent_path = tmp_path / "nonexistent.yaml"
        
        with pytest.raises(FileNotFoundError) as exc_info:
            sanitizer = PIISanitizer.__new__(PIISanitizer)
            sanitizer._patterns = []
            sanitizer._patterns_loaded = False
            sanitizer._config_path = str(nonexistent_path)
            sanitizer.load_patterns(str(nonexistent_path))
        
        assert "not found" in str(exc_info.value).lower()

    def test_load_patterns_empty_yaml(self, tmp_path: Path):
        """Test ValueError when YAML file is empty."""
        empty_yaml = tmp_path / "empty.yaml"
        empty_yaml.write_text("", encoding="utf-8")
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer = PIISanitizer.__new__(PIISanitizer)
            sanitizer._patterns = []
            sanitizer._patterns_loaded = False
            sanitizer._config_path = str(empty_yaml)
            sanitizer.load_patterns(str(empty_yaml))
        
        assert "empty" in str(exc_info.value).lower()

    def test_load_patterns_missing_patterns_key(self, tmp_path: Path):
        """Test ValueError when YAML missing 'patterns' key."""
        invalid_yaml = tmp_path / "invalid.yaml"
        invalid_yaml.write_text("other_key: value", encoding="utf-8")
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer = PIISanitizer.__new__(PIISanitizer)
            sanitizer._patterns = []
            sanitizer._patterns_loaded = False
            sanitizer._config_path = str(invalid_yaml)
            sanitizer.load_patterns(str(invalid_yaml))
        
        assert "patterns" in str(exc_info.value).lower()

    def test_load_patterns_invalid_pattern_structure(self, tmp_path: Path):
        """Test ValueError when pattern objects have invalid structure."""
        invalid_patterns_yaml = tmp_path / "invalid_structure.yaml"
        invalid_content = yaml.dump({
            "patterns": [
                {"pattern": "valid"},  # Missing 'replacement' key
            ]
        })
        invalid_patterns_yaml.write_text(invalid_content, encoding="utf-8")
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer = PIISanitizer.__new__(PIISanitizer)
            sanitizer._patterns = []
            sanitizer._patterns_loaded = False
            sanitizer._config_path = str(invalid_patterns_yaml)
            sanitizer.load_patterns(str(invalid_patterns_yaml))
        
        assert "missing" in str(exc_info.value).lower()

    def test_load_patterns_invalid_regex(self, tmp_path: Path):
        """Test ValueError when regex pattern is malformed."""
        malformed_regex_yaml = tmp_path / "malformed_regex.yaml"
        malformed_content = yaml.dump({
            "patterns": [
                {
                    "pattern": "[unclosed",  # Invalid regex - unclosed bracket
                    "replacement": "[INVALID]"
                }
            ]
        })
        malformed_regex_yaml.write_text(malformed_content, encoding="utf-8")
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer = PIISanitizer.__new__(PIISanitizer)
            sanitizer._patterns = []
            sanitizer._patterns_loaded = False
            sanitizer._config_path = str(malformed_regex_yaml)
            sanitizer.load_patterns(str(malformed_regex_yaml))
        
        assert "regex" in str(exc_info.value).lower() or "pattern" in str(exc_info.value).lower()

    def test_load_patterns_malformed_yaml(self, tmp_path: Path):
        """Test ValueError when YAML syntax is invalid."""
        malformed_yaml = tmp_path / "malformed.yaml"
        malformed_yaml.write_text("invalid: yaml: syntax: [", encoding="utf-8")
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer = PIISanitizer.__new__(PIISanitizer)
            sanitizer._patterns = []
            sanitizer._patterns_loaded = False
            sanitizer._config_path = str(malformed_yaml)
            sanitizer.load_patterns(str(malformed_yaml))
        
        assert "yaml" in str(exc_info.value).lower() or "parse" in str(exc_info.value).lower()

    def test_load_patterns_returns_raw_patterns(self, mock_yaml_file: Path):
        """Test that load_patterns returns list of raw pattern tuples."""
        sanitizer = PIISanitizer.__new__(PIISanitizer)
        sanitizer._patterns = []
        sanitizer._patterns_loaded = False
        sanitizer._config_path = str(mock_yaml_file)
        
        raw_patterns = sanitizer.load_patterns(str(mock_yaml_file))
        
        assert isinstance(raw_patterns, list)
        assert len(raw_patterns) == 6  # UUID, EMAIL, ID-braced, ID, Bearer, token
        for pattern, replacement in raw_patterns:
            assert isinstance(pattern, str)
            assert isinstance(replacement, str)

    def test_reload_patterns(self, mock_yaml_file: Path):
        """Test hot-reload of patterns from configuration file."""
        sanitizer = PIISanitizer(config_path=str(mock_yaml_file))
        initial_count = sanitizer.get_pattern_count()
        
        # Reload patterns
        new_count = sanitizer.reload_patterns()
        
        assert new_count == initial_count
        assert sanitizer._patterns_loaded is True


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

class TestEdgeCases:
    """Test suite for edge cases and boundary conditions."""

    def test_sanitize_empty_string(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization of empty string returns empty string."""
        result = sanitizer_with_patterns.sanitize("")
        assert result == ""

    def test_sanitize_none_value(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization of None value returns None."""
        result = sanitizer_with_patterns.sanitize(None)
        assert result is None

    def test_sanitize_no_pii_text(self, sanitizer_with_patterns: PIISanitizer):
        """Test that text without PII is returned unchanged."""
        clean_text = "This is a clean error message with no PII"
        result = sanitizer_with_patterns.sanitize(clean_text)
        assert result == clean_text

    def test_sanitize_whitespace_only(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization of whitespace-only string."""
        whitespace_text = "   \n\t  "
        result = sanitizer_with_patterns.sanitize(whitespace_text)
        assert result == whitespace_text

    def test_sanitize_with_no_patterns_loaded(self, sanitizer_no_patterns: PIISanitizer):
        """Test sanitization when no patterns loaded returns original text."""
        text_with_pii = "Error for user@example.com"
        result = sanitizer_no_patterns.sanitize(text_with_pii)
        # Should return original text when no patterns loaded
        assert result == text_with_pii

    def test_sanitize_very_long_text(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization of very long text (performance consideration)."""
        # Create text with 10000 characters including PII
        long_text = ("Some error text " * 500) + "user@example.com" + (" more text" * 100)
        result = sanitizer_with_patterns.sanitize(long_text)
        assert "[EMAIL]" in result
        assert "user@example.com" not in result

    def test_sanitize_special_characters(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization with special characters preserved."""
        text_with_special = "Error: user@example.com <script>alert('xss')</script>"
        result = sanitizer_with_patterns.sanitize(text_with_special)
        assert "[EMAIL]" in result
        assert "<script>alert('xss')</script>" in result  # Preserved

    def test_sanitize_unicode_text(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization with Unicode characters."""
        unicode_text = "错误: user@example.com 用户未找到"
        result = sanitizer_with_patterns.sanitize(unicode_text)
        assert "[EMAIL]" in result
        assert "错误:" in result
        assert "用户未找到" in result

    def test_get_pattern_count_no_patterns(self, sanitizer_no_patterns: PIISanitizer):
        """Test pattern count is zero when no patterns loaded."""
        assert sanitizer_no_patterns.get_pattern_count() == 0


# =============================================================================
# DETERMINISTIC OUTPUT TESTS
# =============================================================================

class TestDeterministicOutput:
    """Test suite for deterministic sanitization behavior."""

    def test_same_input_same_output(self, sanitizer_with_patterns: PIISanitizer):
        """
        Test that identical inputs produce identical outputs.
        
        Validates deterministic behavior required for fingerprint stability
        per Section 0.7.1 (User-Specified Requirements).
        """
        input_text = "Error: user@example.com with user_id=12345"
        
        # Run sanitization multiple times
        result1 = sanitizer_with_patterns.sanitize(input_text)
        result2 = sanitizer_with_patterns.sanitize(input_text)
        result3 = sanitizer_with_patterns.sanitize(input_text)
        
        # All results must be identical
        assert result1 == result2 == result3

    def test_order_independence(self, sanitizer_with_patterns: PIISanitizer):
        """Test that pattern application produces consistent results."""
        input_text = "user@example.com with UUID 550e8400-e29b-41d4-a716-446655440000"
        
        # Multiple runs should produce identical results
        results = [sanitizer_with_patterns.sanitize(input_text) for _ in range(5)]
        
        assert all(r == results[0] for r in results)

    def test_idempotency(self, sanitizer_with_patterns: PIISanitizer):
        """Test that sanitizing already-sanitized text is idempotent."""
        input_text = "Error for user@example.com"
        
        # First sanitization
        result1 = sanitizer_with_patterns.sanitize(input_text)
        
        # Second sanitization of already sanitized text
        result2 = sanitizer_with_patterns.sanitize(result1)
        
        # Should be identical (idempotent)
        assert result1 == result2


# =============================================================================
# STACK TRACE PRESERVATION TESTS
# =============================================================================

class TestStackTracePreservation:
    """Test suite for stack trace structure preservation."""

    def test_stack_trace_structure_preserved(self, sanitizer_with_patterns: PIISanitizer):
        """
        Test that stack trace structure is maintained while removing PII.
        
        Per Section 0.7.4: "Preserves stack trace structure while removing
        sensitive values to maintain debugging utility while protecting privacy."
        """
        stack_trace = """
Traceback (most recent call last):
  File "/app/handlers/user.py", line 145, in get_user
    user = db.query(User).filter(User.email == 'user@example.com').first()
  File "/app/db/session.py", line 78, in query
    raise DatabaseError(f"Connection failed for user_id={12345}")
DatabaseError: Connection failed for user_id={12345}
"""
        result = sanitizer_with_patterns.sanitize(stack_trace)
        
        # Verify PII removed
        assert "user@example.com" not in result
        assert "[EMAIL]" in result
        assert "user_id={12345}" not in result or "user_id=[ID]" in result
        
        # Verify structure preserved
        assert "Traceback (most recent call last):" in result
        assert "File \"/app/handlers/user.py\", line 145" in result
        assert "File \"/app/db/session.py\", line 78" in result
        assert "DatabaseError:" in result

    def test_multiline_error_with_pii(self, sanitizer_with_patterns: PIISanitizer):
        """Test multiline error messages with PII on different lines."""
        multiline_error = """
Error processing request:
  User: user@example.com
  Request ID: 550e8400-e29b-41d4-a716-446655440000
  Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
  User ID: user_id=12345
"""
        result = sanitizer_with_patterns.sanitize(multiline_error)
        
        # Verify all PII removed
        assert "user@example.com" not in result
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        
        # Verify placeholders present
        assert "[EMAIL]" in result
        assert "[UUID]" in result
        assert "[TOKEN]" in result
        
        # Verify structure preserved
        assert "Error processing request:" in result
        assert "User:" in result
        assert "Request ID:" in result

    def test_stack_frame_with_variables(self, sanitizer_with_patterns: PIISanitizer):
        """Test stack frame with variable assignments containing PII."""
        stack_frame = """
  File "/app/service.py", line 42, in process
    email = 'admin@example.com'
    request_id = '550e8400-e29b-41d4-a716-446655440000'
    token = 'Bearer abc123xyz'
"""
        result = sanitizer_with_patterns.sanitize(stack_frame)
        
        # Verify PII sanitized
        assert "admin@example.com" not in result
        assert "[EMAIL]" in result
        
        # Verify file path and line numbers preserved
        assert "File \"/app/service.py\", line 42" in result


# =============================================================================
# INTEGRATION-STYLE TESTS
# =============================================================================

class TestIntegrationScenarios:
    """Integration-style tests combining multiple sanitization patterns."""

    def test_comprehensive_pii_sanitization(self, sanitizer_with_patterns: PIISanitizer):
        """Test comprehensive sanitization with all PII types in one text."""
        complex_text = """
Authentication failed for user@example.com
Request ID: 550e8400-e29b-41d4-a716-446655440000
User details: user_id=12345, userId:67890
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
API Key: api_key='sk_live_123456789'
Contact: support@company.org for assistance
"""
        result = sanitizer_with_patterns.sanitize(complex_text)
        
        # Verify all PII types sanitized
        assert "user@example.com" not in result
        assert "support@company.org" not in result
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        assert "12345" not in result
        assert "67890" not in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "sk_live_123456789" not in result
        
        # Verify placeholders present
        assert "[EMAIL]" in result
        assert "[UUID]" in result
        assert "[ID]" in result
        assert "[TOKEN]" in result

    def test_real_world_vercel_error(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization of realistic Vercel error payload."""
        vercel_error = """
TypeError: Cannot read property 'email' of undefined
  at /var/task/pages/api/user.js:45:23
  User email: customer@example.com
  Trace ID: 550e8400-e29b-41d4-a716-446655440000
  Request headers: Authorization: Bearer eyJhbGc...
"""
        result = sanitizer_with_patterns.sanitize(vercel_error)
        
        # Verify error type and location preserved
        assert "TypeError: Cannot read property 'email' of undefined" in result
        assert "/var/task/pages/api/user.js:45:23" in result
        
        # Verify PII sanitized
        assert "[EMAIL]" in result
        assert "[UUID]" in result
        assert "[TOKEN]" in result

    def test_real_world_gcp_error(self, sanitizer_with_patterns: PIISanitizer):
        """Test sanitization of realistic GCP error payload."""
        gcp_error = """
ERROR: Database connection failed
  Service: api-service-00042-xyz
  User: admin@gcp-project.iam.gserviceaccount.com
  Insert ID: abc123-def456-ghi789
  User ID: user_id=987654
"""
        result = sanitizer_with_patterns.sanitize(gcp_error)
        
        # Verify service name preserved
        assert "Service: api-service-00042-xyz" in result
        
        # Verify PII sanitized
        assert "admin@gcp-project.iam.gserviceaccount.com" not in result
        assert "[EMAIL]" in result
        assert "user_id=[ID]" in result


# =============================================================================
# INITIALIZATION TESTS
# =============================================================================

class TestInitialization:
    """Test suite for PIISanitizer initialization scenarios."""

    def test_init_with_custom_config_path(self, mock_yaml_file: Path):
        """Test initialization with custom configuration file path."""
        sanitizer = PIISanitizer(config_path=str(mock_yaml_file))
        assert sanitizer._config_path == str(mock_yaml_file)
        assert sanitizer._patterns_loaded is True

    def test_init_with_default_config_path(self):
        """Test initialization with default configuration path."""
        with patch.object(PIISanitizer, "load_patterns", side_effect=FileNotFoundError):
            sanitizer = PIISanitizer()
            assert sanitizer._config_path == "config/sanitization_patterns.yaml"

    def test_init_handles_load_failure_gracefully(self):
        """Test that initialization continues even if pattern loading fails."""
        with patch.object(PIISanitizer, "load_patterns", side_effect=Exception("Load error")):
            sanitizer = PIISanitizer(config_path="/some/path.yaml")
            # Should not raise exception, continues with empty patterns
            assert sanitizer.get_pattern_count() == 0

    def test_init_logs_success(self, mock_yaml_file: Path, caplog):
        """Test that successful initialization logs pattern count."""
        with caplog.at_level("INFO"):
            sanitizer = PIISanitizer(config_path=str(mock_yaml_file))
        
        # Check that success was logged
        assert any("initialized successfully" in record.message.lower() 
                   for record in caplog.records)

    def test_init_logs_failure(self, caplog):
        """Test that initialization failure is logged."""
        with caplog.at_level("ERROR"):
            with patch.object(PIISanitizer, "load_patterns", side_effect=FileNotFoundError):
                sanitizer = PIISanitizer(config_path="/nonexistent.yaml")
        
        # Check that failure was logged
        assert any("failed to load" in record.message.lower() 
                   for record in caplog.records)


# =============================================================================
# COMPILE PATTERNS TESTS
# =============================================================================

class TestCompilePatterns:
    """Test suite for _compile_patterns internal method."""

    def test_compile_patterns_valid_config(self, valid_patterns_config: List[Dict[str, str]]):
        """Test successful compilation of valid pattern configuration."""
        sanitizer = PIISanitizer.__new__(PIISanitizer)
        compiled, raw = sanitizer._compile_patterns(valid_patterns_config)
        
        assert len(compiled) == 6  # UUID, EMAIL, ID-braced, ID, Bearer, token
        assert len(raw) == 6  # Same patterns in raw format
        
        # Verify compiled patterns are regex Pattern objects
        for pattern, replacement in compiled:
            assert isinstance(pattern, re.Pattern)
            assert isinstance(replacement, str)

    def test_compile_patterns_invalid_dict_structure(self):
        """Test ValueError when pattern is not a dictionary."""
        sanitizer = PIISanitizer.__new__(PIISanitizer)
        invalid_config = ["not a dict", "also not a dict"]
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer._compile_patterns(invalid_config)
        
        assert "not a dictionary" in str(exc_info.value).lower()

    def test_compile_patterns_missing_keys(self):
        """Test ValueError when pattern dict missing required keys."""
        sanitizer = PIISanitizer.__new__(PIISanitizer)
        invalid_config = [{"pattern": "test"}]  # Missing 'replacement'
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer._compile_patterns(invalid_config)
        
        assert "missing" in str(exc_info.value).lower()

    def test_compile_patterns_invalid_regex(self):
        """Test ValueError when regex pattern is invalid."""
        sanitizer = PIISanitizer.__new__(PIISanitizer)
        invalid_config = [
            {
                "pattern": "[unclosed",  # Invalid regex
                "replacement": "[TEST]"
            }
        ]
        
        with pytest.raises(ValueError) as exc_info:
            sanitizer._compile_patterns(invalid_config)
        
        assert "invalid regex" in str(exc_info.value).lower() or "pattern" in str(exc_info.value).lower()


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Test suite for error handling and failure modes."""

    def test_sanitize_with_exception_returns_original_text(self, sanitizer_with_patterns: PIISanitizer):
        """Test that sanitize returns original text if exception occurs during processing."""
        # Mock pattern.sub to raise exception
        original_patterns = sanitizer_with_patterns._patterns
        
        # Create mock pattern that raises exception
        mock_pattern = Mock()
        mock_pattern.sub.side_effect = RuntimeError("Pattern matching error")
        sanitizer_with_patterns._patterns = [(mock_pattern, "[TEST]")]
        
        input_text = "Test error message"
        result = sanitizer_with_patterns.sanitize(input_text)
        
        # Should return original text when exception occurs (fail-open)
        assert result == input_text
        
        # Restore original patterns
        sanitizer_with_patterns._patterns = original_patterns

    def test_sanitize_logs_warning_when_no_patterns(self, sanitizer_no_patterns: PIISanitizer, caplog):
        """Test that warning is logged when sanitization attempted without patterns."""
        with caplog.at_level("WARNING"):
            result = sanitizer_no_patterns.sanitize("Test text")
        
        assert any("no patterns loaded" in record.message.lower() 
                   for record in caplog.records)

    def test_sanitize_logs_debug_info(self, sanitizer_with_patterns: PIISanitizer, caplog):
        """Test that debug information is logged during sanitization."""
        with caplog.at_level("DEBUG"):
            sanitizer_with_patterns.sanitize("Test user@example.com")
        
        # Should log sanitization completion with metrics
        assert any("sanitization completed" in record.message.lower() 
                   for record in caplog.records)


# =============================================================================
# PERFORMANCE CONSIDERATION TESTS
# =============================================================================

class TestPerformanceConsiderations:
    """Test suite for performance-related aspects."""

    def test_pattern_caching(self, mock_yaml_file: Path):
        """Test that compiled patterns are cached in memory."""
        sanitizer = PIISanitizer(config_path=str(mock_yaml_file))
        
        # Get pattern references
        patterns_ref1 = sanitizer._patterns
        
        # Multiple sanitizations should reuse cached patterns
        sanitizer.sanitize("test1")
        sanitizer.sanitize("test2")
        patterns_ref2 = sanitizer._patterns
        
        # Should be same object reference (cached)
        assert patterns_ref1 is patterns_ref2

    def test_no_redundant_pattern_loading(self, mock_yaml_file: Path):
        """Test that patterns are not reloaded on each sanitize call."""
        with patch.object(Path, "read_text") as mock_read:
            mock_read.return_value = """
patterns:
  - pattern: "test"
    replacement: "[TEST]"
"""
            sanitizer = PIISanitizer(config_path=str(mock_yaml_file))
            initial_call_count = mock_read.call_count
            
            # Multiple sanitizations
            sanitizer.sanitize("text1")
            sanitizer.sanitize("text2")
            sanitizer.sanitize("text3")
            
            # read_text should not be called again
            assert mock_read.call_count == initial_call_count
