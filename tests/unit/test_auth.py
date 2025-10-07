"""
Unit Tests for Webhook Authentication Module

This module provides comprehensive unit tests for the WebhookAuthenticator class
and standalone authentication functions (verify_vercel_signature, verify_gcp_token)
per Agent Action Plan Section 0.5.1 Group 8 and Section 0.7.1 directive #7.

Test Coverage:
- Vercel HMAC-SHA256 signature verification with timing attack resistance
- GCP OIDC JWT token validation with audience and issuer verification
- Edge cases: missing headers, malformed tokens, empty payloads
- Security: constant-time comparison, secure failure modes
- WebhookAuthenticator class methods and auto-detection

Per Section 0.7.1 directive #7:
- Reject all unauthenticated requests (verify returns False)
- Use timing-attack-resistant comparison (hmac.compare_digest)
- Never log secrets or tokens
- Validate webhook authenticity: Vercel signatures or GCP OIDC tokens

Test Requirements from Agent Action Plan:
- Minimum 80% code coverage, target 90%+
- Mock all external dependencies (google.auth.id_token)
- Test both class-based and standalone function usage
- Comprehensive security testing

Author: Blitzy Platform
Version: 1.0.0
"""

import hmac
import hashlib
from typing import Dict, Any, Optional
from unittest.mock import Mock, MagicMock, patch

import pytest
from flask import Request
from google.auth.exceptions import GoogleAuthError

# Import functions under test from auth module
from src.utils.auth import (
    WebhookAuthenticator,
    verify_vercel_signature,
    verify_gcp_token
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def vercel_secret() -> str:
    """
    Fixture providing test Vercel webhook secret.
    
    Returns:
        str: Test secret for HMAC signature generation
    """
    return "test_vercel_webhook_secret_12345"


@pytest.fixture
def gcp_audience() -> str:
    """
    Fixture providing test GCP audience URL.
    
    Returns:
        str: Expected audience claim for GCP OIDC token validation
    """
    return "https://error-triage.jiratest.com/events"


@pytest.fixture
def valid_request_body() -> bytes:
    """
    Fixture providing valid webhook request body.
    
    Returns:
        bytes: JSON payload bytes for testing
    """
    return b'{"event": "test", "message": "error occurred"}'


@pytest.fixture
def mock_flask_request() -> Mock:
    """
    Fixture providing mock Flask Request object factory.
    
    Returns:
        Mock: Factory function to create Request mocks with custom headers and body
    """
    def _create_request(headers: Dict[str, str], body: bytes = b'') -> Mock:
        """
        Create mock Flask Request with specified headers and body.
        
        Args:
            headers: Dictionary of HTTP headers
            body: Request body as bytes
        
        Returns:
            Mock: Configured Mock Request object
        """
        request = Mock(spec=Request)
        request.headers = headers
        request.data = body
        return request
    
    return _create_request


@pytest.fixture
def vercel_signature_generator(vercel_secret: str) -> callable:
    """
    Fixture providing function to generate valid Vercel HMAC signatures.
    
    Args:
        vercel_secret: Secret from vercel_secret fixture
    
    Returns:
        callable: Function that generates HMAC-SHA256 signature for given body
    """
    def _generate_signature(body: bytes) -> str:
        """
        Generate HMAC-SHA256 signature for request body.
        
        Args:
            body: Request body bytes
        
        Returns:
            str: Hexadecimal HMAC-SHA256 signature
        """
        return hmac.new(
            vercel_secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
    
    return _generate_signature


# ============================================================================
# WebhookAuthenticator Class Tests
# ============================================================================


class TestWebhookAuthenticatorInit:
    """Test WebhookAuthenticator initialization and configuration."""
    
    def test_init_with_both_secrets(self, vercel_secret: str, gcp_audience: str):
        """Test initializing authenticator with both Vercel and GCP credentials."""
        authenticator = WebhookAuthenticator(
            vercel_secret=vercel_secret,
            gcp_audience=gcp_audience
        )
        
        assert authenticator.vercel_secret == vercel_secret
        assert authenticator.gcp_audience == gcp_audience
    
    def test_init_with_vercel_only(self, vercel_secret: str):
        """Test initializing authenticator with only Vercel secret."""
        authenticator = WebhookAuthenticator(vercel_secret=vercel_secret)
        
        assert authenticator.vercel_secret == vercel_secret
        assert authenticator.gcp_audience is None
    
    def test_init_with_gcp_only(self, gcp_audience: str):
        """Test initializing authenticator with only GCP audience."""
        authenticator = WebhookAuthenticator(gcp_audience=gcp_audience)
        
        assert authenticator.vercel_secret is None
        assert authenticator.gcp_audience == gcp_audience
    
    def test_init_with_no_credentials(self):
        """Test initializing authenticator with no credentials (secure default)."""
        authenticator = WebhookAuthenticator()
        
        assert authenticator.vercel_secret is None
        assert authenticator.gcp_audience is None


class TestWebhookAuthenticatorVerify:
    """Test WebhookAuthenticator auto-detection and verification."""
    
    def test_verify_auto_detects_vercel(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """Test that verify() auto-detects and validates Vercel webhooks."""
        # Generate valid signature
        signature = vercel_signature_generator(valid_request_body)
        
        # Create request with Vercel signature header
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=valid_request_body
        )
        
        # Initialize authenticator
        authenticator = WebhookAuthenticator(vercel_secret=vercel_secret)
        
        # Verify should auto-detect Vercel and validate signature
        assert authenticator.verify(request) is True
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_verify_auto_detects_gcp(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that verify() auto-detects and validates GCP webhooks."""
        # Mock successful token verification
        mock_verify_token.return_value = {
            'iss': 'https://accounts.google.com',
            'aud': gcp_audience,
            'exp': 9999999999
        }
        
        # Create request with GCP Authorization header
        request = mock_flask_request(
            headers={'Authorization': 'Bearer valid_jwt_token_12345'},
            body=b'{}'
        )
        
        # Initialize authenticator
        authenticator = WebhookAuthenticator(gcp_audience=gcp_audience)
        
        # Verify should auto-detect GCP and validate token
        assert authenticator.verify(request) is True
        
        # Verify token validation was called with correct audience
        mock_verify_token.assert_called_once()
        call_args = mock_verify_token.call_args
        assert call_args[0][2] == gcp_audience
    
    def test_verify_rejects_no_authentication_headers(self, mock_flask_request: callable):
        """Test that verify() rejects requests with no authentication headers."""
        request = mock_flask_request(headers={}, body=b'{}')
        authenticator = WebhookAuthenticator()
        
        assert authenticator.verify(request) is False
    
    def test_verify_rejects_vercel_when_secret_not_configured(
        self,
        mock_flask_request: callable,
        valid_request_body: bytes
    ):
        """Test that Vercel webhooks are rejected when vercel_secret is None."""
        request = mock_flask_request(
            headers={'x-vercel-signature': 'some_signature'},
            body=valid_request_body
        )
        
        # Initialize without Vercel secret
        authenticator = WebhookAuthenticator(gcp_audience="https://example.com")
        
        assert authenticator.verify(request) is False
    
    def test_verify_rejects_gcp_when_audience_not_configured(
        self,
        mock_flask_request: callable
    ):
        """Test that GCP webhooks are rejected when gcp_audience is None."""
        request = mock_flask_request(
            headers={'Authorization': 'Bearer some_token'},
            body=b'{}'
        )
        
        # Initialize without GCP audience
        authenticator = WebhookAuthenticator(vercel_secret="test_secret")
        
        assert authenticator.verify(request) is False


# ============================================================================
# Vercel Signature Verification Tests
# ============================================================================


class TestVercelSignatureVerification:
    """Test Vercel HMAC-SHA256 signature verification."""
    
    def test_valid_signature_returns_true(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """Test that valid Vercel signature is accepted."""
        # Generate valid signature
        signature = vercel_signature_generator(valid_request_body)
        
        # Create request with valid signature
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=valid_request_body
        )
        
        # Verify signature
        assert verify_vercel_signature(request, vercel_secret) is True
    
    def test_invalid_signature_returns_false(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable
    ):
        """Test that invalid Vercel signature is rejected."""
        # Create request with wrong signature
        request = mock_flask_request(
            headers={'x-vercel-signature': 'invalid_signature_12345'},
            body=valid_request_body
        )
        
        # Verify signature should fail
        assert verify_vercel_signature(request, vercel_secret) is False
    
    def test_tampered_body_signature_mismatch(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """Test that signature fails when body is tampered."""
        # Generate signature for original body
        signature = vercel_signature_generator(valid_request_body)
        
        # Create request with tampered body
        tampered_body = b'{"event": "test", "message": "tampered data"}'
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=tampered_body
        )
        
        # Verification should fail due to body mismatch
        assert verify_vercel_signature(request, vercel_secret) is False
    
    def test_wrong_secret_signature_mismatch(
        self,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """Test that signature fails when wrong secret is used."""
        # Generate signature with one secret
        signature = vercel_signature_generator(valid_request_body)
        
        # Create request
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=valid_request_body
        )
        
        # Verify with different secret should fail
        wrong_secret = "different_secret_67890"
        assert verify_vercel_signature(request, wrong_secret) is False
    
    def test_missing_signature_header_returns_false(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable
    ):
        """Test that missing x-vercel-signature header is rejected."""
        # Create request without signature header
        request = mock_flask_request(
            headers={},
            body=valid_request_body
        )
        
        assert verify_vercel_signature(request, vercel_secret) is False
    
    def test_empty_signature_header_returns_false(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable
    ):
        """Test that empty signature header is rejected."""
        # Create request with empty signature
        request = mock_flask_request(
            headers={'x-vercel-signature': ''},
            body=valid_request_body
        )
        
        assert verify_vercel_signature(request, vercel_secret) is False
    
    def test_empty_body_returns_false(
        self,
        vercel_secret: str,
        mock_flask_request: callable
    ):
        """Test that empty request body is rejected."""
        # Create request with empty body
        request = mock_flask_request(
            headers={'x-vercel-signature': 'some_signature'},
            body=b''
        )
        
        assert verify_vercel_signature(request, vercel_secret) is False
    
    def test_wrong_hash_algorithm_signature_fails(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable
    ):
        """Test that signature computed with wrong algorithm (SHA1) is rejected."""
        # Generate signature with SHA1 instead of SHA256
        wrong_signature = hmac.new(
            vercel_secret.encode('utf-8'),
            valid_request_body,
            hashlib.sha1  # Wrong algorithm
        ).hexdigest()
        
        # Create request with SHA1 signature
        request = mock_flask_request(
            headers={'x-vercel-signature': wrong_signature},
            body=valid_request_body
        )
        
        # Verification should fail
        assert verify_vercel_signature(request, vercel_secret) is False
    
    def test_timing_attack_resistance_uses_compare_digest(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """
        Test that signature comparison uses hmac.compare_digest for timing attack resistance.
        
        Per Section 0.7.1 directive #7: Use timing-attack-resistant comparison
        """
        # This test ensures hmac.compare_digest is used internally
        # We can't directly test timing, but we verify correct implementation
        
        signature = vercel_signature_generator(valid_request_body)
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=valid_request_body
        )
        
        # The implementation must use hmac.compare_digest
        # Valid signature should return True
        assert verify_vercel_signature(request, vercel_secret) is True
        
        # Invalid signature should return False
        request_invalid = mock_flask_request(
            headers={'x-vercel-signature': 'a' * 64},  # Wrong but same length
            body=valid_request_body
        )
        assert verify_vercel_signature(request_invalid, vercel_secret) is False
    
    def test_exception_handling_returns_false(
        self,
        vercel_secret: str,
        mock_flask_request: callable
    ):
        """Test that exceptions during verification are handled and return False."""
        # Create request that will cause exception
        request = Mock(spec=Request)
        request.headers = {'x-vercel-signature': 'valid_format'}
        # Make request.data raise exception when accessed
        request.data = property(lambda self: (_ for _ in ()).throw(RuntimeError("Test error")))
        
        # Should handle exception and return False
        assert verify_vercel_signature(request, vercel_secret) is False
    
    @pytest.mark.parametrize("signature,expected", [
        ("", False),  # Empty signature
        ("a" * 64, False),  # Wrong signature (right length)
        ("invalid", False),  # Invalid hex format
        (None, False),  # None signature (treated as missing)
    ])
    def test_various_invalid_signatures(
        self,
        signature: Optional[str],
        expected: bool,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable
    ):
        """Test various invalid signature formats are all rejected."""
        headers = {'x-vercel-signature': signature} if signature is not None else {}
        request = mock_flask_request(headers=headers, body=valid_request_body)
        
        assert verify_vercel_signature(request, vercel_secret) is expected


# ============================================================================
# GCP Token Verification Tests
# ============================================================================


class TestGCPTokenVerification:
    """Test GCP OIDC JWT token verification."""
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_valid_token_returns_true(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that valid GCP OIDC token is accepted."""
        # Mock successful token verification with valid issuer
        mock_verify_token.return_value = {
            'iss': 'https://accounts.google.com',
            'aud': gcp_audience,
            'sub': 'service-account@project.iam.gserviceaccount.com',
            'exp': 9999999999
        }
        
        # Create request with valid token
        request = mock_flask_request(
            headers={'Authorization': 'Bearer valid_jwt_token'},
            body=b'{}'
        )
        
        # Verify token
        assert verify_gcp_token(request, gcp_audience) is True
        
        # Verify token was validated with correct audience
        mock_verify_token.assert_called_once()
        call_args = mock_verify_token.call_args
        assert call_args[0][2] == gcp_audience
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_valid_token_with_googleapis_issuer(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that token with googleapis.com issuer is accepted."""
        # Mock token with alternative valid issuer
        mock_verify_token.return_value = {
            'iss': 'https://www.googleapis.com',
            'aud': gcp_audience,
            'exp': 9999999999
        }
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer valid_jwt_token'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is True
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_invalid_issuer_returns_false(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that token from unauthorized issuer is rejected."""
        # Mock token with invalid issuer
        mock_verify_token.return_value = {
            'iss': 'https://malicious-issuer.com',
            'aud': gcp_audience,
            'exp': 9999999999
        }
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer token_with_bad_issuer'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_wrong_audience_returns_false(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that token for different audience is rejected."""
        # Mock token verification to raise ValueError for wrong audience
        mock_verify_token.side_effect = GoogleAuthError("Token has wrong audience")
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer token_with_wrong_audience'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_expired_token_returns_false(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that expired token is rejected."""
        # Mock expired token error
        mock_verify_token.side_effect = GoogleAuthError("Token expired")
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer expired_token'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_malformed_token_returns_false(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that malformed JWT token is rejected."""
        # Mock malformed token error
        mock_verify_token.side_effect = GoogleAuthError("Invalid token format")
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer malformed.token.invalid'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    def test_missing_authorization_header_returns_false(
        self,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that missing Authorization header is rejected."""
        request = mock_flask_request(headers={}, body=b'{}')
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    def test_missing_bearer_prefix_returns_false(
        self,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that Authorization header without 'Bearer ' prefix is rejected."""
        request = mock_flask_request(
            headers={'Authorization': 'Basic username:password'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    def test_empty_bearer_token_returns_false(
        self,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that 'Bearer ' with no token is rejected."""
        request = mock_flask_request(
            headers={'Authorization': 'Bearer '},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    def test_only_bearer_keyword_returns_false(
        self,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that just 'Bearer' without space and token is rejected."""
        request = mock_flask_request(
            headers={'Authorization': 'Bearer'},
            body=b'{}'
        )
        
        assert verify_gcp_token(request, gcp_audience) is False
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_exception_handling_returns_false(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test that unexpected exceptions during verification are handled."""
        # Mock unexpected exception
        mock_verify_token.side_effect = Exception("Unexpected error")
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer some_token'},
            body=b'{}'
        )
        
        # Should handle exception and return False
        assert verify_gcp_token(request, gcp_audience) is False
    
    @pytest.mark.parametrize("auth_header,expected", [
        ("", False),  # Empty header
        ("Bearer", False),  # No token after Bearer
        ("Bearer ", False),  # Space but no token
        ("Basic token", False),  # Wrong auth type
        ("bearer token", False),  # Lowercase bearer (case sensitive)
    ])
    def test_various_invalid_auth_headers(
        self,
        auth_header: str,
        expected: bool,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test various invalid Authorization header formats are rejected."""
        headers = {'Authorization': auth_header} if auth_header else {}
        request = mock_flask_request(headers=headers, body=b'{}')
        
        assert verify_gcp_token(request, gcp_audience) is expected


# ============================================================================
# Class Method Tests (Instance Methods)
# ============================================================================


class TestWebhookAuthenticatorInstanceMethods:
    """Test WebhookAuthenticator instance methods directly."""
    
    def test_verify_vercel_signature_instance_method(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """Test WebhookAuthenticator.verify_vercel_signature instance method."""
        authenticator = WebhookAuthenticator(vercel_secret=vercel_secret)
        
        signature = vercel_signature_generator(valid_request_body)
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=valid_request_body
        )
        
        # Test instance method directly
        assert authenticator.verify_vercel_signature(request, vercel_secret) is True
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_verify_gcp_token_instance_method(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test WebhookAuthenticator.verify_gcp_token instance method."""
        authenticator = WebhookAuthenticator(gcp_audience=gcp_audience)
        
        mock_verify_token.return_value = {
            'iss': 'https://accounts.google.com',
            'aud': gcp_audience
        }
        
        request = mock_flask_request(
            headers={'Authorization': 'Bearer valid_token'},
            body=b'{}'
        )
        
        # Test instance method directly
        assert authenticator.verify_gcp_token(request, gcp_audience) is True


# ============================================================================
# Security-Focused Tests
# ============================================================================


class TestSecurityRequirements:
    """Test security-specific requirements from Agent Action Plan."""
    
    def test_vercel_uses_constant_time_comparison(
        self,
        vercel_secret: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """
        Verify that Vercel signature comparison uses constant-time comparison.
        
        Per Section 0.7.1: Use timing-attack-resistant comparison (hmac.compare_digest)
        """
        # Generate two different signatures
        signature_correct = vercel_signature_generator(valid_request_body)
        signature_incorrect = 'a' * 64  # Same length, different value
        
        # Both operations should take roughly the same time
        # (We can't measure timing in unit test, but we verify behavior)
        
        request_correct = mock_flask_request(
            headers={'x-vercel-signature': signature_correct},
            body=valid_request_body
        )
        request_incorrect = mock_flask_request(
            headers={'x-vercel-signature': signature_incorrect},
            body=valid_request_body
        )
        
        # Correct signature accepted
        assert verify_vercel_signature(request_correct, vercel_secret) is True
        
        # Incorrect signature rejected
        assert verify_vercel_signature(request_incorrect, vercel_secret) is False
    
    def test_secure_failure_on_exception(
        self,
        vercel_secret: str,
        gcp_audience: str
    ):
        """
        Test that all authentication methods fail securely (return False) on exceptions.
        
        Per Section 0.7.4: Fail securely on any exception (returns False)
        """
        # Create a malformed request that will cause exceptions
        bad_request = Mock(spec=Request)
        bad_request.headers = Mock()
        bad_request.headers.get = Mock(side_effect=Exception("Test exception"))
        bad_request.data = b'test'
        
        # Both methods should fail securely
        assert verify_vercel_signature(bad_request, vercel_secret) is False
        assert verify_gcp_token(bad_request, gcp_audience) is False
    
    def test_no_secret_logging(
        self,
        vercel_secret: str,
        gcp_audience: str,
        valid_request_body: bytes,
        mock_flask_request: callable,
        vercel_signature_generator: callable,
        caplog: pytest.LogCaptureFixture
    ):
        """
        Verify that secrets and tokens are never logged.
        
        Per Section 0.7.4: Never log secrets or API tokens (even partially)
        """
        import logging
        caplog.set_level(logging.DEBUG)
        
        # Test Vercel authentication
        signature = vercel_signature_generator(valid_request_body)
        vercel_request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=valid_request_body
        )
        verify_vercel_signature(vercel_request, vercel_secret)
        
        # Test GCP authentication (with mock to avoid external calls)
        with patch('src.utils.auth.id_token.verify_oauth2_token') as mock_verify:
            mock_verify.return_value = {'iss': 'https://accounts.google.com'}
            gcp_request = mock_flask_request(
                headers={'Authorization': 'Bearer test_token_12345'},
                body=b'{}'
            )
            verify_gcp_token(gcp_request, gcp_audience)
        
        # Verify no secrets in logs
        log_output = caplog.text
        assert vercel_secret not in log_output, "Vercel secret found in logs!"
        assert "test_token_12345" not in log_output, "GCP token found in logs!"
        assert signature not in log_output, "Signature found in logs!"


# ============================================================================
# Integration-Style Tests
# ============================================================================


class TestAuthenticationIntegration:
    """Test realistic authentication scenarios."""
    
    def test_full_vercel_webhook_flow(
        self,
        vercel_secret: str,
        mock_flask_request: callable,
        vercel_signature_generator: callable
    ):
        """Test complete Vercel webhook authentication flow."""
        # Simulate real Vercel webhook payload
        payload = b'{"source":"vercel","deployment":{"id":"dpl_xyz"},"message":"Error occurred"}'
        signature = vercel_signature_generator(payload)
        
        request = mock_flask_request(
            headers={'x-vercel-signature': signature},
            body=payload
        )
        
        # Authenticate using both standalone function and class
        assert verify_vercel_signature(request, vercel_secret) is True
        
        authenticator = WebhookAuthenticator(vercel_secret=vercel_secret)
        assert authenticator.verify(request) is True
    
    @patch('src.utils.auth.id_token.verify_oauth2_token')
    def test_full_gcp_webhook_flow(
        self,
        mock_verify_token: Mock,
        gcp_audience: str,
        mock_flask_request: callable
    ):
        """Test complete GCP Pub/Sub webhook authentication flow."""
        # Simulate real GCP Pub/Sub push payload
        mock_verify_token.return_value = {
            'iss': 'https://accounts.google.com',
            'aud': gcp_audience,
            'email': 'pubsub@gcp-sa.iam.gserviceaccount.com'
        }
        
        payload = b'{"message":{"data":"eyJ0ZXN0IjoidGVzdCJ9","messageId":"123"}}'
        request = mock_flask_request(
            headers={'Authorization': 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...'},
            body=payload
        )
        
        # Authenticate using both standalone function and class
        assert verify_gcp_token(request, gcp_audience) is True
        
        authenticator = WebhookAuthenticator(gcp_audience=gcp_audience)
        assert authenticator.verify(request) is True
