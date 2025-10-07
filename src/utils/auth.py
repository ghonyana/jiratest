"""
Webhook Authentication Module for Error Triage Service

This module implements HMAC-SHA256 signature verification for Vercel webhooks
and OIDC JWT token validation for GCP Pub/Sub push subscriptions, ensuring
only authenticated webhook requests from authorized sources are processed by
the /events endpoint.

Per Section 0.1.2 Security Constraints, the service MUST validate webhook
authenticity via Vercel signatures or GCP OIDC tokens. All unauthenticated
requests are rejected with 401 Unauthorized status.

Authentication Methods:
- Vercel: HMAC-SHA256 signature verification using x-vercel-signature header
- GCP: OIDC JWT token validation with audience and issuer verification

Security Features:
- Constant-time comparison (hmac.compare_digest) to prevent timing attacks
- Secure failure: default to False on any exception
- No logging of secrets or tokens (even partially) per Section 0.7.4
- Structured logging of authentication attempts with correlation fields

Usage Examples:

    # Class-based usage with WebhookAuthenticator
    authenticator = WebhookAuthenticator(
        vercel_secret="webhook_secret_from_secrets_manager",
        gcp_audience="https://error-triage.jiratest.com/events"
    )
    
    # Auto-detect source and verify
    if not authenticator.verify(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Standalone function usage in Flask before_request
    from utils.auth import verify_vercel_signature, verify_gcp_token
    
    @app.before_request
    def authenticate():
        if request.path == '/events':
            if 'x-vercel-signature' in request.headers:
                if not verify_vercel_signature(request, VERCEL_SECRET):
                    return jsonify({'error': 'Invalid signature'}), 401
            elif 'Authorization' in request.headers:
                if not verify_gcp_token(request, GCP_AUDIENCE):
                    return jsonify({'error': 'Invalid token'}), 401
            else:
                return jsonify({'error': 'Missing authentication'}), 401

Integration with Monitoring:
- Logs authentication attempts with action field (webhook_authenticated, webhook_auth_failed)
- Increments errors_total metric with error_type='webhook_auth_failed' on failures
- Includes source identification and timestamp for audit trail

Per Section 0.7.5:
- Vercel webhook signature format: HMAC-SHA256 hex digest in x-vercel-signature header
- GCP Pub/Sub push token format: Bearer JWT in Authorization header
- Token audience must match service endpoint URL
- Token issuer must be Google (accounts.google.com or googleapis.com)

Author: Blitzy Platform
Version: 1.0.0
"""

import hmac
import hashlib
from typing import Optional

from flask import Request
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google.auth.exceptions import GoogleAuthError

from utils.logging_config import get_logger
from utils.metrics_collector import increment_error


# Initialize structured logger for authentication events
logger = get_logger(__name__)


class WebhookAuthenticator:
    """
    Webhook authentication service for Vercel and GCP webhook validation.
    
    Provides unified authentication interface with auto-detection of webhook source
    and appropriate verification method. Supports both Vercel HMAC signature
    verification and GCP OIDC JWT token validation.
    
    Per Section 0.7.4 Security-Specific Patterns, this class implements:
    - Constant-time comparison for HMAC signatures (timing-attack resistant)
    - Secure JWT token validation with audience and issuer verification
    - Comprehensive error handling with structured logging
    - Metrics tracking for authentication failures
    
    Attributes:
        vercel_secret (Optional[str]): Shared secret for Vercel webhook signature verification
        gcp_audience (Optional[str]): Expected audience claim for GCP OIDC token validation
    
    Example:
        # Initialize with secrets from AWS Secrets Manager
        authenticator = WebhookAuthenticator(
            vercel_secret=os.getenv("VERCEL_WEBHOOK_SECRET"),
            gcp_audience="https://error-triage.jiratest.com/events"
        )
        
        # Verify webhook in route handler
        @app.route('/events', methods=['POST'])
        def handle_event():
            if not authenticator.verify(request):
                return jsonify({'error': 'Unauthorized'}), 401
            # Process authenticated webhook
    """
    
    def __init__(self, vercel_secret: Optional[str] = None, gcp_audience: Optional[str] = None):
        """
        Initialize WebhookAuthenticator with authentication credentials.
        
        Args:
            vercel_secret: Shared secret for Vercel webhook HMAC signature verification.
                          Loaded from AWS Secrets Manager: jira/jiratest/{env}/webhook-secret
            gcp_audience: Expected audience claim for GCP OIDC JWT token validation.
                         Should match service endpoint URL (e.g., https://error-triage.jiratest.com/events)
        
        Note:
            At least one of vercel_secret or gcp_audience should be provided.
            If both are None, all webhook authentication will fail (secure default).
        """
        self.vercel_secret = vercel_secret
        self.gcp_audience = gcp_audience
        
        # Log initialization (never log actual secret values)
        logger.info(
            "WebhookAuthenticator initialized",
            extra={
                "action": "authenticator_initialized",
                "vercel_enabled": vercel_secret is not None,
                "gcp_enabled": gcp_audience is not None
            }
        )
    
    def verify(self, request: Request) -> bool:
        """
        Auto-detect webhook source and verify authentication.
        
        Examines request headers to determine webhook source (Vercel or GCP)
        and applies appropriate authentication method:
        - Vercel: x-vercel-signature header present -> HMAC verification
        - GCP: Authorization Bearer header present -> OIDC JWT verification
        
        Args:
            request: Flask Request object containing headers and body
        
        Returns:
            bool: True if authentication successful, False otherwise
        
        Example:
            if not authenticator.verify(request):
                logger.warning("Unauthorized webhook attempt")
                return jsonify({'error': 'Unauthorized'}), 401
        """
        # Check for Vercel signature header
        if 'x-vercel-signature' in request.headers:
            if self.vercel_secret is None:
                logger.warning(
                    "Vercel webhook received but vercel_secret not configured",
                    extra={"action": "webhook_auth_failed", "source": "vercel"}
                )
                return False
            return self.verify_vercel_signature(request, self.vercel_secret)
        
        # Check for GCP Authorization header
        elif 'Authorization' in request.headers:
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                if self.gcp_audience is None:
                    logger.warning(
                        "GCP webhook received but gcp_audience not configured",
                        extra={"action": "webhook_auth_failed", "source": "gcp"}
                    )
                    return False
                return self.verify_gcp_token(request, self.gcp_audience)
        
        # No recognized authentication headers
        logger.warning(
            "Webhook received with no recognized authentication headers",
            extra={"action": "webhook_auth_failed", "source": "unknown"}
        )
        return False
    
    def verify_vercel_signature(self, request: Request, secret: str) -> bool:
        """
        Verify Vercel webhook HMAC-SHA256 signature.
        
        Implements HMAC-SHA256 signature verification per Section 0.7.4:
        1. Extract x-vercel-signature header from request
        2. Read raw request body as bytes (critical for signature validation)
        3. Compute HMAC-SHA256: hmac.new(secret.encode(), request.data, hashlib.sha256).hexdigest()
        4. Compare signatures using hmac.compare_digest() for timing-attack resistance
        
        Per Section 0.7.5 Vercel Integration:
        - Signature header: x-vercel-signature
        - Signature format: HMAC-SHA256 hex digest
        - Body must be read as raw bytes for verification
        
        Args:
            request: Flask Request object with x-vercel-signature header and body data
            secret: Shared secret for HMAC computation (from AWS Secrets Manager)
        
        Returns:
            bool: True if signature is valid, False otherwise
        
        Example:
            if verify_vercel_signature(request, vercel_secret):
                logger.info("Vercel webhook authenticated")
                process_vercel_event(request.json)
        
        Security:
            - Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks
            - Never logs secret values (even partially)
            - Fails securely on any exception (returns False)
        """
        try:
            # Extract signature from header
            signature = request.headers.get('x-vercel-signature')
            if not signature:
                logger.warning(
                    "Vercel webhook missing x-vercel-signature header",
                    extra={"action": "webhook_auth_failed", "source": "vercel"}
                )
                increment_error('production', 'webhook_auth_failed')
                return False
            
            # Read raw request body as bytes (critical for signature validation)
            body = request.data
            if not body:
                logger.warning(
                    "Vercel webhook has empty body",
                    extra={"action": "webhook_auth_failed", "source": "vercel"}
                )
                increment_error('production', 'webhook_auth_failed')
                return False
            
            # Compute HMAC-SHA256 signature
            expected_signature = hmac.new(
                secret.encode('utf-8'),
                body,
                hashlib.sha256
            ).hexdigest()
            
            # Compare using constant-time comparison (timing-attack resistant)
            if hmac.compare_digest(signature, expected_signature):
                logger.info(
                    "Vercel webhook authenticated successfully",
                    extra={"action": "webhook_authenticated", "source": "vercel"}
                )
                return True
            else:
                logger.warning(
                    "Vercel webhook signature mismatch",
                    extra={"action": "webhook_auth_failed", "source": "vercel"}
                )
                increment_error('production', 'webhook_auth_failed')
                return False
        
        except Exception as e:
            # Fail securely: default to False on any exception
            # Log error but don't expose internal details
            logger.error(
                "Exception during Vercel signature verification",
                extra={
                    "action": "webhook_auth_failed",
                    "source": "vercel",
                    "error_type": "signature_verification_error"
                },
                exc_info=True
            )
            increment_error('production', 'webhook_auth_failed')
            return False
    
    def verify_gcp_token(self, request: Request, audience: str) -> bool:
        """
        Verify GCP Pub/Sub push OIDC JWT token.
        
        Implements OIDC JWT token validation per Section 0.7.4:
        1. Extract Authorization header with Bearer token format
        2. Validate header format: 'Bearer <JWT_TOKEN>'
        3. Use google.auth.jwt.decode() for OIDC token validation
        4. Verify token audience matches service endpoint URL
        5. Verify issuer is 'https://accounts.google.com' or 'https://www.googleapis.com'
        
        Per Section 0.7.5 GCP Integration:
        - Authorization header: Bearer <JWT_TOKEN>
        - Token audience: Service endpoint URL
        - Valid issuers: accounts.google.com, googleapis.com
        
        Args:
            request: Flask Request object with Authorization Bearer header
            audience: Expected audience claim (service endpoint URL)
        
        Returns:
            bool: True if token is valid, False otherwise
        
        Example:
            if verify_gcp_token(request, "https://error-triage.jiratest.com/events"):
                logger.info("GCP webhook authenticated")
                process_gcp_event(request.json)
        
        Security:
            - Validates token signature using Google's public keys
            - Verifies audience claim to prevent token reuse
            - Verifies issuer to ensure token from Google
            - Never logs token values (even partially)
            - Fails securely on any exception (returns False)
        """
        try:
            # Extract Authorization header
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                logger.warning(
                    "GCP webhook missing or malformed Authorization header",
                    extra={"action": "webhook_auth_failed", "source": "gcp"}
                )
                increment_error('production', 'webhook_auth_failed')
                return False
            
            # Extract token from header
            token = auth_header[len('Bearer '):]
            if not token:
                logger.warning(
                    "GCP webhook has empty Bearer token",
                    extra={"action": "webhook_auth_failed", "source": "gcp"}
                )
                increment_error('production', 'webhook_auth_failed')
                return False
            
            # Verify OIDC JWT token using Google's public keys
            # This validates signature, expiration, and audience
            info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience
            )
            
            # Verify issuer is Google
            issuer = info.get('iss')
            valid_issuers = [
                'https://accounts.google.com',
                'https://www.googleapis.com'
            ]
            
            if issuer not in valid_issuers:
                logger.warning(
                    f"GCP webhook token has invalid issuer: {issuer}",
                    extra={
                        "action": "webhook_auth_failed",
                        "source": "gcp",
                        "error_type": "invalid_issuer"
                    }
                )
                increment_error('production', 'webhook_auth_failed')
                return False
            
            # Token is valid
            logger.info(
                "GCP webhook authenticated successfully",
                extra={
                    "action": "webhook_authenticated",
                    "source": "gcp"
                }
            )
            return True
        
        except GoogleAuthError as e:
            # Handle Google authentication-specific errors
            # (ExpiredSignatureError, InvalidTokenError, etc.)
            logger.warning(
                f"GCP token validation failed: {str(e)}",
                extra={
                    "action": "webhook_auth_failed",
                    "source": "gcp",
                    "error_type": "token_validation_error"
                }
            )
            increment_error('production', 'webhook_auth_failed')
            return False
        
        except Exception as e:
            # Fail securely: default to False on any exception
            # Log error but don't expose internal details
            logger.error(
                "Exception during GCP token verification",
                extra={
                    "action": "webhook_auth_failed",
                    "source": "gcp",
                    "error_type": "token_verification_error"
                },
                exc_info=True
            )
            increment_error('production', 'webhook_auth_failed')
            return False


# ============================================================================
# Standalone Functions for Direct Usage
# ============================================================================


def verify_vercel_signature(request: Request, secret: str) -> bool:
    """
    Standalone function for Vercel webhook signature verification.
    
    This function provides the same HMAC-SHA256 signature verification as
    WebhookAuthenticator.verify_vercel_signature() but as a standalone
    function for direct usage in Flask route handlers or before_request hooks.
    
    Per Section 0.7.4 Security-Specific Patterns:
    - Extracts x-vercel-signature header from request
    - Reads raw request body as bytes (critical for signature validation)
    - Computes HMAC-SHA256: hmac.new(secret.encode(), request.data, hashlib.sha256).hexdigest()
    - Compares signatures using hmac.compare_digest() for timing-attack resistance
    
    Args:
        request: Flask Request object with x-vercel-signature header and body data
        secret: Shared secret for HMAC computation (from AWS Secrets Manager)
    
    Returns:
        bool: True if signature is valid, False otherwise
    
    Example - Flask before_request usage:
        from flask import Flask, request, jsonify
        from utils.auth import verify_vercel_signature
        
        app = Flask(__name__)
        VERCEL_SECRET = get_secret("vercel_webhook_secret")
        
        @app.before_request
        def authenticate():
            if request.path == '/events' and 'x-vercel-signature' in request.headers:
                if not verify_vercel_signature(request, VERCEL_SECRET):
                    return jsonify({'error': 'Invalid signature'}), 401
    
    Example - Route handler usage:
        @app.route('/events', methods=['POST'])
        def handle_event():
            if not verify_vercel_signature(request, VERCEL_SECRET):
                return jsonify({'error': 'Invalid signature'}), 401
            process_event(request.json)
            return jsonify({'status': 'accepted'}), 202
    
    Security:
        - Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks
        - Never logs secret values (even partially) per Section 0.7.4
        - Fails securely on any exception (returns False)
        - Logs authentication failures with structured context
    """
    # Delegate to WebhookAuthenticator instance method
    # This ensures consistent behavior between class and standalone usage
    authenticator = WebhookAuthenticator(vercel_secret=secret)
    return authenticator.verify_vercel_signature(request, secret)


def verify_gcp_token(request: Request, audience: str) -> bool:
    """
    Standalone function for GCP Pub/Sub push OIDC token verification.
    
    This function provides the same OIDC JWT token validation as
    WebhookAuthenticator.verify_gcp_token() but as a standalone function
    for direct usage in Flask route handlers or before_request hooks.
    
    Per Section 0.7.4 Security-Specific Patterns:
    - Extracts Authorization header with Bearer token format
    - Validates header format: 'Bearer <JWT_TOKEN>'
    - Uses google.oauth2.id_token.verify_oauth2_token() for OIDC token validation
    - Verifies token audience matches service endpoint URL
    - Verifies issuer is 'https://accounts.google.com' or 'https://www.googleapis.com'
    
    Args:
        request: Flask Request object with Authorization Bearer header
        audience: Expected audience claim (service endpoint URL)
    
    Returns:
        bool: True if token is valid, False otherwise
    
    Example - Flask before_request usage:
        from flask import Flask, request, jsonify
        from utils.auth import verify_gcp_token
        
        app = Flask(__name__)
        GCP_AUDIENCE = "https://error-triage.jiratest.com/events"
        
        @app.before_request
        def authenticate():
            if request.path == '/events' and 'Authorization' in request.headers:
                if not verify_gcp_token(request, GCP_AUDIENCE):
                    return jsonify({'error': 'Invalid token'}), 401
    
    Example - Route handler usage:
        @app.route('/events', methods=['POST'])
        def handle_event():
            if not verify_gcp_token(request, GCP_AUDIENCE):
                return jsonify({'error': 'Invalid token'}), 401
            process_event(request.json)
            return jsonify({'status': 'accepted'}), 202
    
    Security:
        - Validates token signature using Google's public keys
        - Verifies audience claim to prevent token reuse
        - Verifies issuer to ensure token from Google
        - Never logs token values (even partially) per Section 0.7.4
        - Fails securely on any exception (returns False)
        - Logs authentication failures with structured context
    """
    # Delegate to WebhookAuthenticator instance method
    # This ensures consistent behavior between class and standalone usage
    authenticator = WebhookAuthenticator(gcp_audience=audience)
    return authenticator.verify_gcp_token(request, audience)


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    'WebhookAuthenticator',
    'verify_vercel_signature',
    'verify_gcp_token'
]
